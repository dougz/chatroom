#!/usr/bin/python3

import argparse
import io
import re
import os
import enchant


class TextTransform:
  def __init__(self, input_file):
    self.word_dict = enchant.Dict("en_US")
    self.word_dict.add("spam")
    self.word_dict.add("dit")
    self.speaker2_dict = self.parse_text_file(input_file)


  def parse_text_file(self, input_file):
       with open(input_file, "r") as f:

        text = f.read()
        text = text.translate({ord(i): None for i in '.,!?;:()'})
        text = text.translate({ord(i): " " for i in '-'})
        text = text.lower()

        return text.split()


  def validate_word(self, text):
    text = text.strip('.,!?;:()')
    return self.word_dict.check(text.lower())

  def index_text(self, text):
    sentences = re.split('[.?!]', text)
    out = []

    for sentence in sentences:
      words = sentence.split()
      total = 0
      for word in words:
        if self.validate_word(word):
          sum = 0
          for letter in word.lower():
            if (ord(letter) >= ord('a') and ord(letter) <= ord('z')):
               sum = sum + ord(letter) - ord('a') + 1
 #           else:
 #               print ("Ignoring: " + str(letter))
          total = total + sum
#          print("\"" + word + "\" sums to " + str(sum) + " total is " + str(total))
        else:
#          print("Ignoring invalid word: " + word)
          # just to be consistent with how everything else works
          out.append(word)


      if (total < len(self.speaker2_dict) and total > 0):
        # our index is 1 based
        out.append(self.speaker2_dict[total - 1])

    return " ".join(out)


  def reverse_text(self, text):
     words = text.split()
     out = []

     for word in words:
        if self.validate_word(word):
          out.append(word[::-1])
        else:
          out.append(word)
     return " ".join(out)

  def translate_to_french(self,text):
    return "Not implemented"

  def transform(self, speaker, text):

      if speaker == 1:
        return self.translate_to_french(text)
  #      return await self.translate_to_french(text)
      elif speaker == 2:
        # Index into the 
        return self.index_text(text)
      elif speaker == 3:
        # Reverse words
        return self.reverse_text(text)
      else:
        return text


def main():
  parser = argparse.ArgumentParser(
    description=("Test Crosschat speaker transforms"))
  parser.add_argument("--speaker", type=int, default=2, help="Which speaker to test (2, 3 or 4).")
  parser.add_argument("--input_file", default="declaration.txt",
                      help="File source for speaker 2.")  
 
  options = parser.parse_args()

  if options.input_file:
      transformer = TextTransform(options.input_file)

      user_input = ""
      while user_input != "_quit_":
        user_input = input("Enter input from speaker " + str(options.speaker) + " (\'_quit_\' to quit): ").strip()

        print("SPEAKER " + str(options.speaker) + ": " + transformer.transform(int(options.speaker), user_input))

      print("Goodbye!")

  

if __name__ == "__main__":
  main()
