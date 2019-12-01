#!/usr/bin/python3

import argparse
import asyncio
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

import scrum


def transform_text(speaker, text):
  if speaker == 1:
    # Remove vowels
    return re.sub(r"[aoeuiAOEUI]", "", text)
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


class GameState:
  SPEAKER_COUNT = 6

  BY_TEAM = {}

  @classmethod
  def set_globals(cls, options):
    cls.options = options

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
    alt_text = transform_text(speaker, text)

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
  def get(self, fn):
    if fn.endswith(".css"):
      self.set_header("Content-Type", "text/css")
    elif fn.endswith(".js"):
      self.set_header("Content-Type", "application/javascript")
    with open(fn) as f:
      self.write(f.read())


def make_app(options):
  GameState.set_globals(options)

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

  options = parser.parse_args()

  app = ChatroomApp(options, make_app(options))
  app.start()


if __name__ == "__main__":
  main()

