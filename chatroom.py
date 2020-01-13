#!/usr/bin/python3

import argparse
import asyncio
import base64
import collections
import http.client
import itertools
import json
import os
import random
import re
import string
import time
import unicodedata

import enchant
import tornado.web
import tornado.httpclient


from cryptography.hazmat.backends import default_backend
from cryptography.hazmat.primitives.asymmetric import rsa, padding
from cryptography.hazmat.primitives import serialization, hashes

import scrum


class TextTransform:
  def __init__(self, oauth2, text_file):
    self.oauth2 = oauth2
    self.client = tornado.httpclient.AsyncHTTPClient()

    self.declaration_index = [None]
    self.english = enchant.Dict("en_US")
    self.english.add("spam")

    self.alpha = {}
    for i, k in enumerate(string.ascii_lowercase):
      self.alpha[k] = i+1
    for i, k in enumerate(string.ascii_uppercase):
      self.alpha[k] = i+1

    with open(text_file) as f:
      text = f.read()
      for w in re.finditer(r"(?:[\w']+(?:-[\w']+)?)", text):
        self.declaration_index.append(w.group(0).lower())
        if self.declaration_index[-1] in ("friends", "with", "benefits"):
          print(len(self.declaration_index)-1, self.declaration_index[-1])

  async def translate_to_french(self, text):
    out = []
    for i, w in enumerate(re.split(r"([^\w']+)", text)):
      if not w: continue
      if i % 2 == 0:
        if self.english.check(w):
          out.append(w.lower())
        else:
          out.append("*" * len(w))
      else:
        out.append(w)

    if out:
      norm = "".join(out).strip()

      d = {"q": norm, "target": "fr", "format": "text", "source": "en"}
      for retry in range(2):
        token = await self.oauth2.get()
        req = tornado.httpclient.HTTPRequest(
          "https://translation.googleapis.com/language/translate/v2",
          method="POST",
          body=json.dumps(d),
          headers={"Authorization": token,
                   "Content-Type": "application/json; charset=utf-8"})
        response = await self.client.fetch(req, raise_error=False)
        if response.code == 401:
          # oauth token expired; fetch a new one and try again
          self.oauth2.invalidate()
          continue
        elif response.code == 200:
          # success
          break
        else:
          print(f"translate failed: {response}\n{response.body}")
          return ""

      j = json.loads(response.body)
      try:
        result = j["data"]["translations"][0]["translatedText"]
        print(f"google: [{norm}] --> [{result}]")
        return result
      except (KeyError, IndexError):
        print("failed to read result")
        print(j)

    return ""

  def use_declaration(self, text):
    print("-------------------------")
    sentences = re.split(r"[.!?]", text)
    out = []
    for s in sentences:
      total = 0
      for w in re.finditer(r"\w+", s):
        w = w.group(0)
        if self.english.check(w):
          print(f"{w} good")
          total += sum(self.alpha.get(k, 0) for k in w)
        else:
          print(f"{w} bad")
          out.append("*" * len(w))
      if 0 < total < len(self.declaration_index):
        out.append(self.declaration_index[total])
      print(f"sentence [{s}] total [{total}] out [{' '.join(out)}]")
    return " ".join(out)


  async def transform(self, speaker, text):
    if speaker == 1:
      result = await self.translate_to_french(text)
      print(f"translate {text} --> {result}")
      return result
    elif speaker == 2:
      return self.use_declaration(text)
    elif speaker == 3:
      # Reverse words
      def flip(m):
        w = m.group(0)
        if self.english.check(w):
          return w[::-1]
        else:
          return "*" * len(w)
      return re.sub(r"\w+", flip, text)
    else:
      return text


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
    sig = self.private_key.sign(to_sign, padding.PKCS1v15(), hashes.SHA256())
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


class Clue:
  def __init__(self, text, answer, response):
    self.text = text
    self.answer = answer
    self.response = response

class Round:
  def __init__(self, *clues):
    self.clues = clues

class GameState:
  SPEAKER_COUNT = 3

  BY_TEAM = {}

  @classmethod
  def set_globals(cls, options, text_transform, rounds):
    cls.options = options
    cls.text_transform = text_transform
    cls.rounds = rounds

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
    self.cond = asyncio.Condition()

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
    self.current_clue = None

    await asyncio.sleep(10.0)

    await self.mayor_say("Settle down, you varmints! I’m callin’ this here "
                         "town hall meeting to order!! I’m yer mayor, and let "
                         "me tell you, I have never seen a town hall this full "
                         "of chitter-chatter. I can barely hear what anyone’s "
                         "sayin!")
    await asyncio.sleep(2.0)

    for r in self.rounds:
      self.solved = set()
      for c in itertools.cycle(r.clues):
        if len(self.solved) == len(r.clues):
          break
        if c.answer in self.solved: continue

        self.current_clue = c
        await self.mayor_say(c.text)

        deadline = time.time() + 30
        async with self.cond:
          while c.answer not in self.solved:
            timeout = deadline - time.time()
            if timeout <= 0: break
            try:
              await asyncio.wait_for(self.cond.wait(), timeout)
            except asyncio.TimeoutError:
              pass

        self.current_clue = None
        if c.answer in self.solved:
          await self.mayor_say(c.response)
        else:
          if len(self.solved) < len(r.clues)-1:
            await self.mayor_say("All right, we'll come back to that one later.")
            await asyncio.sleep(3.0)

    await self.mayor_say("Thanks for participating in tonight’s debate. As your "
                         "participation prize, have some toy BAZOOKAS.")

  async def try_answer(self, text):
    canonical = " ".join(re.findall(r"\w+", text.upper()))
    async with self.cond:
      if self.current_clue and canonical == self.current_clue.answer:
        self.solved.add(canonical)
        self.cond.notify_all()

  async def mayor_say(self, text):
    d = {"method": "add_chat", "who": "Mayor", "text": text}
    await self.team.send_messages([d], sticky=1)

  async def send_chat(self, session, text):
    speaker, wids = self.sessions.get(session, (None, None))

    print(f"speaker {speaker} wids {wids} says [{text}]")
    text = text.lower()

    if self.options.debug:
      if text.startswith("1:"):
        speaker = 1
        text = text[2:]
        wids = []
      elif text.startswith("2:"):
        speaker = 2
        text = text[2:]
        wids = []
      elif text.startswith("3:"):
        speaker = 3
        text = text[2:]
        wids = []

    if not speaker: return
    alt_text = await self.text_transform.transform(speaker, text)

    d = {"method": "add_chat",
         "who": f"Speaker {speaker}",
         "text": text,
         "alt": alt_text,
         "wids": list(wids)}
    await self.team.send_messages([d])

    await self.try_answer(alt_text)


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
    if fn.endswith(".css"):
      self.set_header("Content-Type", "text/css")
    elif fn.endswith(".js"):
      self.set_header("Content-Type", "application/javascript")
    with open(fn) as f:
      self.write(f.read())


async def test_translate(text_transform):
  result = await text_transform.translate_to_french("hello, world")
  print(result)


# deploy.sh replaces the following comment with built-in credentials
# when this is packaged for distribution.

# CREDENTIALS HERE


def make_app(options):
  rounds = []
  rounds.append(Round(
    Clue("Anyway, the first agenda item! Last Wednesday, Miss Elizabeth over "
         "at the Rusty Nail performed one of my favorite ditties, “If I "
         "Could Turn Back Time”.  How would you describe exactly what it "
         "is that she did? [4 4]",
         "SANG CHER",
         "That’s right, you French little devil! She SANG CHER. Glad to see "
         "you’re pickin’ up some of our language in this here town!"),
    Clue("Second item -- the schoolhouse is gettin’ a new shipment of "
         "books in. I think a bunch of ‘em is by that Russian fella -- "
         "Doc Tolstoy, I reckon? What was that famous one that he published "
         "in 1869? [3 3 5]",
         "WAR AND PEACE",
         "Professor, you talk too high an’ mighty for my tastes, but you "
         "got it. WAR AND PEACE!"),
    Clue("Third item. Our sheep is gettin’ rustled and eaten by that big "
         "grey furry son-of-a-gun that lives up there by them hills. "
         "I got a right mind to go over there, fill him with some "
         "buckshot, and throw him into a pot and enjoy some of what? [4 4]",
         "WOLF STEW",
         "I can never understand a word that’s comin’ out of yer mouth, "
         "kid, but I agree. WOLF STEW.")))
  rounds.append(Round(
    Clue("Fourth item -- Madame Zoosa, over in the fortune teller’s booth, "
         "says that she can see a future with movin’ pictures, where "
         "people will give awards for the best movin’ pictures around. "
         "She says in 1997, this one might get that award... [3 7 7]",
         "THE ENGLISH PATIENT",
         "No idea what that means, but the Madame says that THE ENGLISH "
         "PATIENT is correct!"),
    Clue("Miss Jenkins over at the General Store got a shipment of ladies "
         "underthings last month, but they are not flying off the shelves the "
         "way she had hoped.  She’s trying to clear inventory, so what would we "
         "call what she is running now? [4 4]",
         "BRAS SALE",
         "Yeah, I guess we could say she was running a BRAS SALE. Correct!"),
    Clue("Sixth item. We got some rascals from the Cutler Gang messin’ "
         "up our dogs and cats, those no-good varmints. They done came "
         "into town and took a knife to poor Mr. Walter’s dog’s feet! "
         "And Miss Kent’s cat, too! What would y’all say that they do? "
         "[4 6 4]",
         "STAB ANIMAL PAWS",
         "Yeah, those Cutler boys got nothin’ better to do, it seems, "
         "than STAB ANIMAL PAWS.")))
  rounds.append(Round(
    Clue("On the grapevine, it looks like Old Tom and the widow Jensen "
         "are gettin’ mighty close. I wouldn’t say they’re a couple, but "
         "more like they like to hang out, and take advantage of each "
         "other’s company, if you get my meanin’. What would you call "
         "an arrangement like that? [7 4 8]",
         "FRIENDS WITH BENEFITS",
         "FRIENDS WITH BENEFITS, that’s a good name for it."),
    Clue("The scouts we sent out gone and done somethin’ stupid. They "
         "spilled a pot of coffee all over them constellation charts "
         "they’ve been usin’ to navigate. Now what does poor Lettie "
         "the Cartographer got to do? [6 4 4]",
         "REDRAW STAR MAPS",
         "Yeah, I reckon she’s gonna have to REDRAW STAR MAPS."),
    Clue("Over at the bank, they’ve been lookin’ to mint some new "
         "money. I can’t say I really agree with them -- they want "
         "to mint money that’s twenty-five to the dollar! You ever "
         "hear of somethin’ as crazy as all that? What would you even "
         "call those? [4 4 5]",
         "FOUR CENT COINS",
         "FOUR CENT COINS! That’s right.")))

  try:
    creds = BUILTIN_CREDENTIALS
  except NameError:
    assert options.credentials
    with open(options.credentials) as f:
      creds = json.load(f)

  text_transform = TextTransform(Oauth2Token(creds), options.declaration_text)
  GameState.set_globals(options, text_transform, rounds)

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
  parser.add_argument("--declaration_text", default=None,
                      help="Declaration of Independence for speaker 2")

  options = parser.parse_args()

  tornado.httpclient.AsyncHTTPClient.configure(
    "tornado.curl_httpclient.CurlAsyncHTTPClient")

  app = ChatroomApp(options, make_app(options))
  app.start()


if __name__ == "__main__":
  main()

