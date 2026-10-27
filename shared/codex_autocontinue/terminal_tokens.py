"""Incremental VT tokenization, not a terminal renderer; bytes are never rewritten."""
import codecs


class TerminalTokens:
    """Keep UTF-8 and escape sequences intact across arbitrary read boundaries."""

    def __init__(self):
        self.decoder = codecs.getincrementaldecoder("utf-8")("replace")
        self.state = "text"
        self.sequence = ""
        self.string_kind = "ignored"
        self.characters_seen = 0

    def feed(self, data):
        for char in self.decoder.decode(data):
            self.characters_seen += 1
            if self.state == "text":
                if char == "\x1b":
                    self.state = "escape"
                else:
                    yield ("text", char)
            elif self.state == "escape":
                self.state = {"[": "csi", "]": "string", "P": "string",
                              "_": "string", "^": "string"}.get(char, "text")
                self.sequence = ""
                self.string_kind = "osc" if char == "]" else "ignored"
                if char in "()*+,-./#%":
                    self.state = "charset"
            elif self.state == "charset":
                self.state = "text"
            elif self.state == "csi":
                self.sequence += char
                if "@" <= char <= "~":
                    yield ("csi", self.sequence)
                    self.sequence = ""
                    self.state = "text"
                elif len(self.sequence) > 256:
                    self.state = "text"
                    self.sequence = ""
            elif self.state == "string":
                if char == "\x07":
                    yield (self.string_kind, self.sequence)
                    self.state = "text"
                elif char == "\x1b":
                    self.state = "string_end"
                elif len(self.sequence) < 1024:
                    self.sequence += char
                else:
                    self.string_kind = "ignored"
            elif self.state == "string_end":
                if char == "\\":
                    yield (self.string_kind, self.sequence)
                    self.state = "text"
                else:
                    self.state = "string"
