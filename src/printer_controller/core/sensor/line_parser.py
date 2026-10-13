"""Extract a number from a line sent by an instrument.

Pure functions, no serial access: the same code checks the example typed by the user and
reads the real lines.
"""

import re
from dataclasses import dataclass

NUMBER = re.compile(r"[-+]?(?:\d+\.?\d*|\.\d+)(?:[eE][-+]?\d+)?")
MODES = ("number", "field", "after_text", "regex")
SEPARATORS = {"none": "", "dot": ".", "comma": ",", "semicolon": ";", "colon": ":", "space": " ",
              "tab": "\t", "custom": None}


class ParseError(Exception):
    """`code` identifies the problem (translated by the interface)."""

    def __init__(self, code, **details):
        super().__init__(code)
        self.code, self.details = code, details


@dataclass
class ParserConfig:
    mode: str = "number"          # number | field | after_text | regex
    separator: str = "space"      # key of SEPARATORS (mode "field")
    custom_separator: str = ""
    position: int = 1             # 1 = first number / first field
    text: str = ""                # mode "after_text": the text that precedes the value
    regex: str = ""               # mode "regex": group 1 (or the whole match) is the value
    decimal: str = "."            # decimal mark used by the instrument: "." or ","

    @classmethod
    def from_dict(cls, data):
        base = cls()
        for key, default in base.__dict__.items():
            value = (data or {}).get(key, default)
            setattr(base, key, int(value) if isinstance(default, int) else str(value))
        return base

    def to_dict(self):
        return dict(self.__dict__)


def _numbers(text, decimal):
    """All the numbers found in `text`."""
    if decimal == ",":
        text = re.sub(r"(?<=\d),(?=\d)", ".", text)
    return NUMBER.findall(text)


def _first_number(text, decimal):
    found = _numbers(text, decimal)
    if not found:
        raise ParseError("no_number", text=text)
    return float(found[0])


def extract(line, config):
    """The value of `line` according to `config`; raises ParseError."""
    line = line.strip()
    if not line:
        raise ParseError("empty")
    mode = config.mode
    if mode == "number":
        found = _numbers(line, config.decimal)
        if not found:
            raise ParseError("no_number", text=line)
        if config.position < 1 or config.position > len(found):
            raise ParseError("position", position=config.position, count=len(found))
        return float(found[config.position - 1])
    if mode == "field":
        separator = config.custom_separator if config.separator == "custom" else SEPARATORS[config.separator]
        if separator in ("", None):
            fields = [line]
        elif separator == " ":
            fields = line.split()                       # several spaces count as one
        else:
            fields = line.split(separator)
        if config.position < 1 or config.position > len(fields):
            raise ParseError("position", position=config.position, count=len(fields))
        return _first_number(fields[config.position - 1], config.decimal)
    if mode == "after_text":
        if not config.text:
            raise ParseError("no_text")
        index = line.find(config.text)
        if index < 0:
            raise ParseError("text_not_found", text=config.text)
        return _first_number(line[index + len(config.text):], config.decimal)
    if mode == "regex":
        try:
            match = re.search(config.regex, line)
        except re.error as exc:
            raise ParseError("bad_regex", error=str(exc)) from None
        if not match:
            raise ParseError("no_match")
        token = match.group(1) if match.groups() else match.group(0)
        return _first_number(token or "", config.decimal)
    raise ParseError("bad_mode", mode=mode)
