"""Two-pass assembler for Intel 4004 assembly language.

Syntax summary (case-insensitive):

    ; comment
    LABEL:  MNEMONIC operand, operand     ; "LABEL," also accepted
    NAME    EQU  expr                     ; or  NAME = expr
            ORG  expr
            DB   1, 2, 0x3F, "TEXT", 'A'  ; also DATA / BYTE
            END

Registers: R0-R15 (or a plain 0-15 expression).
Register pairs: P0-P7, 0P-7P, R0R1 ... R14R15 (or a plain 0-7 expression).
JCN conditions: a 0-15 number or TZ/T0, TN/T1, C1/CY/C, C0/NC, AZ/Z,
AN/NZ.  Pseudo jumps: JZ JNZ JC JNC JT JNT expand to JCN.
Numbers: 42, 0x2A, $2A, 2AH, 0b101010, 101010B, 'A'.  ``$`` alone is the
current address.  Expressions use + - * / % & | ^ << >> ~ ( ).
"""

from __future__ import annotations

import ast
import operator
import re
from dataclasses import dataclass, field


class AsmError(Exception):
    pass


REG_OPS = {"INC": 0x60, "ADD": 0x80, "SUB": 0x90, "LD": 0xA0, "XCH": 0xB0}
PAIR_OPS = {"SRC": 0x21, "FIN": 0x30, "JIN": 0x31}
IMM4_OPS = {"BBL": 0xC0, "LDM": 0xD0}
IMPLIED = {
    "NOP": 0x00,
    "WRM": 0xE0, "WMP": 0xE1, "WRR": 0xE2, "WPM": 0xE3,
    "WR0": 0xE4, "WR1": 0xE5, "WR2": 0xE6, "WR3": 0xE7,
    "SBM": 0xE8, "RDM": 0xE9, "RDR": 0xEA, "ADM": 0xEB,
    "RD0": 0xEC, "RD1": 0xED, "RD2": 0xEE, "RD3": 0xEF,
    "CLB": 0xF0, "CLC": 0xF1, "IAC": 0xF2, "CMC": 0xF3,
    "CMA": 0xF4, "RAL": 0xF5, "RAR": 0xF6, "TCC": 0xF7,
    "DAC": 0xF8, "TCS": 0xF9, "STC": 0xFA, "DAA": 0xFB,
    "KBP": 0xFC, "DCL": 0xFD,
}
CONDITIONS = {
    "TZ": 1, "T0": 1, "TN": 9, "T1": 9,
    "C1": 2, "CY": 2, "C": 2, "C0": 10, "NC": 10,
    "AZ": 4, "Z": 4, "AN": 12, "NZ": 12,
}
JUMP_ALIASES = {"JT": 1, "JNT": 9, "JC": 2, "JNC": 10, "JZ": 4, "JNZ": 12}
TWO_BYTE = {"JCN", "FIM", "JUN", "JMS", "ISZ", *JUMP_ALIASES}
DATA_DIRECTIVES = {"DB", "DATA", "BYTE"}
MNEMONICS = (set(REG_OPS) | set(PAIR_OPS) | set(IMM4_OPS) | set(IMPLIED)
             | TWO_BYTE | DATA_DIRECTIVES | {"ORG", "END", "EQU"})

_BINOPS = {
    ast.Add: operator.add, ast.Sub: operator.sub, ast.Mult: operator.mul,
    ast.Div: operator.floordiv, ast.FloorDiv: operator.floordiv,
    ast.Mod: operator.mod, ast.BitAnd: operator.and_, ast.BitOr: operator.or_,
    ast.BitXor: operator.xor, ast.LShift: operator.lshift,
    ast.RShift: operator.rshift,
}
_UNOPS = {ast.USub: operator.neg, ast.UAdd: operator.pos, ast.Invert: operator.invert}

_NUM_RE = re.compile(
    r"'(\\?.)'"                           # character literal
    r"|\$([0-9A-Fa-f]+)\b"                # $1F
    r"|\b([0-9][0-9A-Fa-f]*)[Hh]\b"       # 1FH
    r"|\b([01]+)[Bb]\b"                   # 1010B
    r"|(\$)"                              # current location
)


@dataclass
class Line:
    number: int
    text: str
    label: str | None = None
    op: str | None = None
    operands: list[str] = field(default_factory=list)
    address: int = 0
    data: bytes = b""


@dataclass
class Program:
    image: bytearray
    symbols: dict[str, int]
    lines: list[Line]
    used: set[int]

    def listing(self) -> str:
        out = []
        for ln in self.lines:
            hexes = " ".join(f"{b:02X}" for b in ln.data)
            addr = f"{ln.address:03X}" if ln.data or ln.label else "   "
            out.append(f"{ln.number:4d}  {addr}  {hexes:<8}  {ln.text}")
        return "\n".join(out)

    def binary(self) -> bytes:
        """ROM image trimmed to the highest used address."""
        top = max(self.used) + 1 if self.used else 0
        return bytes(self.image[:top])


def _split_operands(s: str) -> list[str]:
    parts, cur, quote = [], "", None
    for ch in s:
        if quote:
            cur += ch
            if ch == quote:
                quote = None
        elif ch in "'\"":
            quote = ch
            cur += ch
        elif ch == ",":
            parts.append(cur.strip())
            cur = ""
        else:
            cur += ch
    if cur.strip():
        parts.append(cur.strip())
    return parts


def _strip_comment(s: str) -> str:
    quote = None
    for i, ch in enumerate(s):
        if quote:
            if ch == quote:
                quote = None
        elif ch in "'\"":
            quote = ch
        elif ch == ";":
            return s[:i]
    return s


class Assembler:
    def __init__(self):
        self.symbols: dict[str, int] = {}

    # ----------------------------------------------------------- expressions
    def eval(self, expr: str, here: int, ln: Line, required: bool = True) -> int:
        def repl(m):
            if m.group(1) is not None:
                ch = m.group(1)
                return str(ord(ch[-1]) if not ch.startswith("\\") else
                           ord({"n": "\n", "r": "\r", "t": "\t", "0": "\0"}.get(ch[1], ch[1])))
            for g in (2, 3):
                if m.group(g):
                    return str(int(m.group(g), 16))
            if m.group(4):
                return str(int(m.group(4), 2))
            return "__HERE__"

        src = _NUM_RE.sub(repl, expr.strip())
        try:
            tree = ast.parse(src, mode="eval")
        except SyntaxError:
            raise AsmError(f"line {ln.number}: bad expression '{expr}'")

        def ev(node):
            if isinstance(node, ast.Expression):
                return ev(node.body)
            if isinstance(node, ast.Constant) and isinstance(node.value, int):
                return node.value
            if isinstance(node, ast.BinOp) and type(node.op) in _BINOPS:
                return _BINOPS[type(node.op)](ev(node.left), ev(node.right))
            if isinstance(node, ast.UnaryOp) and type(node.op) in _UNOPS:
                return _UNOPS[type(node.op)](ev(node.operand))
            if isinstance(node, ast.Name):
                name = node.id.upper()
                if name == "__HERE__":
                    return here
                if name in self.symbols:
                    return self.symbols[name]
                if required:
                    raise AsmError(f"line {ln.number}: undefined symbol '{node.id}'")
                return 0
            raise AsmError(f"line {ln.number}: unsupported expression '{expr}'")

        return ev(tree)

    def _range(self, value: int, lo: int, hi: int, what: str, ln: Line) -> int:
        if not lo <= value <= hi:
            raise AsmError(f"line {ln.number}: {what} {value} out of range {lo}-{hi}")
        return value

    def _reg(self, s: str, ln: Line) -> int:
        m = re.fullmatch(r"R(\d+)", s.strip(), re.I)
        v = int(m.group(1)) if m else self.eval(s, ln.address, ln)
        return self._range(v, 0, 15, "register", ln)

    def _pair(self, s: str, ln: Line) -> int:
        t = s.strip().upper()
        m = re.fullmatch(r"P(\d)|(\d)P", t)
        if m:
            v = int(m.group(1) or m.group(2))
        elif (m := re.fullmatch(r"R(\d+)R(\d+)", t)):
            a, b = int(m.group(1)), int(m.group(2))
            if a % 2 or b != a + 1:
                raise AsmError(f"line {ln.number}: bad register pair '{s}'")
            v = a // 2
        else:
            v = self.eval(s, ln.address, ln)
        return self._range(v, 0, 7, "register pair", ln)

    def _cond(self, s: str, ln: Line) -> int:
        t = s.strip().upper()
        if t in CONDITIONS:
            return CONDITIONS[t]
        return self._range(self.eval(s, ln.address, ln), 0, 15, "condition", ln)

    def _page_target(self, s: str, ln: Line) -> int:
        target = self.eval(s, ln.address, ln) & 0xFFF
        page = (ln.address + 2) & 0xF00
        if target & 0xF00 != page:
            raise AsmError(
                f"line {ln.number}: target 0x{target:03X} is not in page 0x{page:03X} "
                f"(JCN/ISZ can only jump within the current ROM page)")
        return target & 0xFF

    def _need(self, ln: Line, n: int) -> None:
        if len(ln.operands) != n:
            raise AsmError(f"line {ln.number}: {ln.op} expects {n} operand(s), "
                           f"got {len(ln.operands)}")

    # ---------------------------------------------------------------- passes
    def _parse(self, source: str) -> list[Line]:
        lines = []
        for n, raw in enumerate(source.splitlines(), 1):
            ln = Line(n, raw.rstrip())
            body = _strip_comment(raw).strip()
            # label: or label,
            # ("name:" is always a label; "name," only if not a mnemonic)
            m = re.match(r"([A-Za-z_.][\w.]*)\s*([:,])\s*(.*)$", body)
            if m and (m.group(2) == ":" or m.group(1).upper() not in MNEMONICS):
                ln.label, body = m.group(1).upper(), m.group(3)
            # NAME = expr / NAME EQU expr
            m = re.match(r"([A-Za-z_.][\w.]*)\s*(?:=|\s+EQU\s+)(.*)$", body, re.I)
            if m and m.group(1).upper() not in MNEMONICS:
                ln.label, ln.op, ln.operands = m.group(1).upper(), "EQU", [m.group(2)]
            elif body:
                parts = body.split(None, 1)
                ln.op = parts[0].upper()
                ln.operands = _split_operands(parts[1]) if len(parts) > 1 else []
            lines.append(ln)
        return lines

    def _data(self, ln: Line, required: bool) -> bytes:
        out = bytearray()
        for item in ln.operands:
            if len(item) >= 2 and item[0] == item[-1] == '"':
                out += item[1:-1].encode("latin-1").decode("unicode_escape").encode("latin-1")
            else:
                out.append(self.eval(item, ln.address, ln, required) & 0xFF)
        return bytes(out)

    def assemble(self, source: str) -> Program:
        lines = self._parse(source)

        # Pass 1: assign addresses and define symbols.
        pc = 0
        for ln in lines:
            ln.address = pc
            if ln.op == "EQU":
                self.symbols[ln.label] = self.eval(ln.operands[0], pc, ln, required=False)
                continue
            if ln.label:
                if ln.label in self.symbols:
                    raise AsmError(f"line {ln.number}: duplicate label '{ln.label}'")
                self.symbols[ln.label] = pc
            if ln.op is None:
                continue
            if ln.op == "END":
                break
            if ln.op == "ORG":
                pc = ln.address = self.eval(ln.operands[0], pc, ln) & 0xFFF
                if ln.label:
                    self.symbols[ln.label] = pc
            elif ln.op in DATA_DIRECTIVES:
                pc += len(self._data(ln, required=False))
            elif ln.op in TWO_BYTE:
                pc += 2
            elif ln.op in MNEMONICS:
                pc += 1
            else:
                raise AsmError(f"line {ln.number}: unknown mnemonic '{ln.op}'")

        # Re-evaluate EQUs now that every label is known.
        for ln in lines:
            if ln.op == "EQU":
                self.symbols[ln.label] = self.eval(ln.operands[0], ln.address, ln)

        # Pass 2: emit code.
        image = bytearray(4096)
        used: set[int] = set()
        for ln in lines:
            if ln.op == "END":
                break
            if ln.op in (None, "EQU", "ORG"):
                continue
            ln.data = self._encode(ln)
            for i, b in enumerate(ln.data):
                a = ln.address + i
                if a > 0xFFF:
                    raise AsmError(f"line {ln.number}: code past end of ROM (0xFFF)")
                if a in used:
                    raise AsmError(f"line {ln.number}: overlaps code at 0x{a:03X}")
                image[a] = b
                used.add(a)
        return Program(image, dict(self.symbols), lines, used)

    def _encode(self, ln: Line) -> bytes:
        op, ops = ln.op, ln.operands
        if op in DATA_DIRECTIVES:
            return self._data(ln, required=True)
        if op in IMPLIED:
            self._need(ln, 0)
            return bytes([IMPLIED[op]])
        if op in REG_OPS:
            self._need(ln, 1)
            return bytes([REG_OPS[op] | self._reg(ops[0], ln)])
        if op in PAIR_OPS:
            self._need(ln, 1)
            return bytes([PAIR_OPS[op] | (self._pair(ops[0], ln) << 1)])
        if op in IMM4_OPS:
            self._need(ln, 1)
            v = self._range(self.eval(ops[0], ln.address, ln), 0, 15, "4-bit value", ln)
            return bytes([IMM4_OPS[op] | v])
        if op == "FIM":
            self._need(ln, 2)
            v = self.eval(ops[1], ln.address, ln)
            if not 0 <= v <= 0xFFF:
                raise AsmError(f"line {ln.number}: FIM data {v} out of range")
            return bytes([0x20 | (self._pair(ops[0], ln) << 1), v & 0xFF])
        if op in ("JUN", "JMS"):
            self._need(ln, 1)
            v = self.eval(ops[0], ln.address, ln)
            self._range(v, 0, 0xFFF, "address", ln)
            return bytes([(0x40 if op == "JUN" else 0x50) | (v >> 8), v & 0xFF])
        if op == "JCN":
            self._need(ln, 2)
            return bytes([0x10 | self._cond(ops[0], ln), self._page_target(ops[1], ln)])
        if op in JUMP_ALIASES:
            self._need(ln, 1)
            return bytes([0x10 | JUMP_ALIASES[op], self._page_target(ops[0], ln)])
        if op == "ISZ":
            self._need(ln, 2)
            return bytes([0x70 | self._reg(ops[0], ln), self._page_target(ops[1], ln)])
        raise AsmError(f"line {ln.number}: unknown mnemonic '{op}'")


def assemble(source: str) -> Program:
    return Assembler().assemble(source)
