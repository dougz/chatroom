#!/usr/bin/python3

import argparse
import asyncio
import base64
import collections
import itertools
import json
import os
import random
import re
import time
import unicodedata

import http.client
import tornado.web
import tornado.httpclient

from cryptography.hazmat.backends import default_backend
from cryptography.hazmat.primitives.asymmetric import rsa, padding
from cryptography.hazmat.primitives import serialization, hashes

import scrum


class TextTransform:
  def __init__(self, oauth2):
    self.oauth2 = oauth2
    self.client = tornado.httpclient.AsyncHTTPClient()

  async def translate_to_french(self, text):
    token = await self.oauth2.get()
    d = {"q": text, "target": "fr", "format": "text"}
    req = tornado.httpclient.HTTPRequest(
      "https://translation.googleapis.com/language/translate/v2",
      method="POST",
      body=json.dumps(d),
      headers={"Authorization": token,
               "Content-Type": "application/json; charset=utf-8"})
    try:
      response = await self.client.fetch(req)
    except tornado.httpclient.HTTPClientError as e:
      print("translate failed")
      print(e)
      print(e.response)
      print(e.response.body)
      return ""

    j = json.loads(response.body)
    try:
      return j["data"]["translations"][0]["translatedText"]
    except (KeyError, IndexError):
      print("failed to read result")
      print(j)
      return ""

  async def transform(self, speaker, text):
    if speaker == 1:
      return await self.translate_to_french(text)
    elif speaker == 2:
      # Remove consonants
      return re.sub(r"[bcdfghjklmnpqrstvwxyzBCDFGHJKLMNPQRSTVWXYZ]", "", text)
    elif speaker == 3:
      # SHOUTING
      return text.upper()
    elif speaker == 4:
      # Reverse words
      return re.sub(r"\w+", lambda m: m.group(0)[::-1], text)
    elif speaker == 5:
      # Only dots
      return re.sub(r"\w", ".", text)
    elif speaker == 6:
      # Rot13
      def rot13(m):
        x = m.group(0)
        if "A" <= x <= "M" or "a" <= x <= "m":
          return chr(ord(x)+13)
        else:
          return chr(ord(x)-13)
      return re.sub(r"[A-Za-z]", rot13, text)
    else:
      return text


# deploy.sh replaces the following comment with built-in credentials
# when this is packaged for distribution.

# CREDENTIALS HERE

class Oauth2Token:
  def __init__(self, creds):
    self.private_key = serialization.load_pem_private_key(
      creds["private_key"].encode("utf-8"), password=None, backend=default_backend())
    self.client_email = creds["client_email"]
    self.client = tornado.httpclient.AsyncHTTPClient()
    self.mu = asyncio.Lock()
    self.cached = None

  async def get(self):
    async with self.mu:
      if not self.cached:
        self.cached = await self._get_auth_token()
    return self.cached

  def invalidate(self):
    self.cached = None

  async def _get_auth_token(self):
    header = b"{\"alg\":\"RS256\",\"typ\":\"JWT\"}"
    h = base64.urlsafe_b64encode(header)

    now = int(time.time())
    claims = {
      "iss": self.client_email,
      "scope": "https://www.googleapis.com/auth/cloud-translation",
      "aud": "https://www.googleapis.com/oauth2/v4/token",
      "exp": now + 3600,
      "iat": now,
    }

    cs = base64.urlsafe_b64encode(json.dumps(claims).encode("utf-8"))

    to_sign = h + b"." + cs

    sig = self.private_key.sign(to_sign,
                           padding.PKCS1v15(),
                           hashes.SHA256())
    sig = base64.urlsafe_b64encode(sig)

    jwt = to_sign + b"." + sig

    req = tornado.httpclient.HTTPRequest(
      "https://www.googleapis.com/oauth2/v4/token",
      method="POST",
      body=(b"grant_type=urn%3Aietf%3Aparams%3Aoauth%3Agrant-type%3Ajwt-bearer&" +
            b"assertion=" + jwt),
      headers={"Content-Type": "application/x-www-form-urlencoded"})

    try:
      response = await self.client.fetch(req)
    except tornado.httpclient.HTTPClientError as e:
      print("oauth2 token fetch failed")
      print(e)
      print(e.response)
      return None

    j = json.loads(response.body.decode("utf-8"))
    token = j["token_type"] + " " + j["access_token"]
    return token



class GameState:
  SPEAKER_COUNT = 6

  BY_TEAM = {}

  @classmethod
  def set_globals(cls, options, text_transform):
    cls.options = options
    cls.text_transform = text_transform

  @classmethod
  def get_for_team(cls, team):
    if team not in cls.BY_TEAM:
      cls.BY_TEAM[team] = cls(team)
    return cls.BY_TEAM[team]

  def __init__(self, team):
    self.team = team
    self.sessions = {}
    self.running = False
    self.next_speaker = 1

    self.solved = set()
    self.widq = collections.deque()
    self.wids = {}

  async def on_wait(self, session, wid):
    now = time.time()
    self.widq.append((wid, now))

    if session not in self.sessions:
      self.sessions[session] = (self.next_speaker, {wid})
      self.next_speaker += 1
      if self.next_speaker > self.SPEAKER_COUNT:
        self.next_speaker = 1
    else:
      self.sessions[session][1].add(wid)

  async def run_game(self):
    pass

  async def send_chat(self, session, text):
    speaker, wids = self.sessions.get(session, (None, None))
    if not speaker: return
    alt_text = await self.text_transform.transform(speaker, text)

    d = {"method": "add_chat",
         "text": f"Speaker {speaker}: {text}",
         "alt": f"Speaker {speaker}: {alt_text}",
         "wids": list(wids)}
    await self.team.send_messages([d])


class ChatroomApp(scrum.ScrumApp):
  async def on_wait(self, team, session, wid):
    gs = GameState.get_for_team(team)

    if not gs.running:
      gs.running = True
      self.add_callback(gs.run_game)

    await gs.on_wait(session, wid)


class SubmitHandler(tornado.web.RequestHandler):
  def prepare(self):
    self.args = json.loads(self.request.body)

  async def post(self):
    scrum_app = self.application.settings["scrum_app"]
    team, session = await scrum_app.check_cookie(self)
    gs = GameState.get_for_team(team)

    text = self.args["text"]
    await gs.send_chat(session, text)

    self.set_status(http.client.NO_CONTENT.value)

class DebugHandler(tornado.web.RequestHandler):
  async def get(self, fn):
    print("testing translation...")
    await test_translate(GameState.text_transform)
    print("done")
    if fn.endswith(".css"):
      self.set_header("Content-Type", "text/css")
    elif fn.endswith(".js"):
      self.set_header("Content-Type", "application/javascript")
    with open(fn) as f:
      self.write(f.read())


async def test_translate(text_transform):
  result = await text_transform.translate_to_french("hello, world")
  print(result)


def make_app(options):
  try:
    creds = BUILTIN_CREDENTIALS
  except NameError:
    assert options.credentials
    with open(options.credentials) as f:
      creds = json.load(f)

  text_transform = TextTransform(Oauth2Token(creds))
  GameState.set_globals(options, text_transform)

  handlers = [
    (r"/chatsubmit", SubmitHandler),
  ]
  if options.debug:
    handlers.append((r"/chatdebug/(\S+)", DebugHandler))
  return handlers


def main():
  parser = argparse.ArgumentParser(description="Run the chatroom puzzle.")
  parser.add_argument("--debug", action="store_true",
                      help="Run in debug mode.")
  parser.add_argument("-c", "--cookie_secret",
                      default="snellen2020",
                      help="Secret used to create session cookies.")
  parser.add_argument("--listen_port", type=int, default=2007,
                      help="Port requests from frontend.")
  parser.add_argument("--wait_url", default="chatwait",
                      help="Path for wait requests from frontend.")
  parser.add_argument("--main_server_port", type=int, default=2020,
                      help="Port to use for requests to main server.")
  parser.add_argument("--credentials", default=None,
                      help="JSON file with credentials private key.")

  options = parser.parse_args()

  tornado.httpclient.AsyncHTTPClient.configure(
    "tornado.curl_httpclient.CurlAsyncHTTPClient")

  app = ChatroomApp(options, make_app(options))
  app.start()


if __name__ == "__main__":
  main()

