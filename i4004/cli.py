"""Command-line front end: assemble, run, trace and debug 4004 programs."""

from __future__ import annotations

import argparse
import os
import shlex
import sys
from pathlib import Path

from . import describe
from .assembler import AsmError, Program, assemble
from .cpu import CPUError, Intel4004, disassemble

ACCESSIBLE_ENV = "I4004_ACCESSIBLE"


def is_accessible(args) -> bool:
    if getattr(args, "accessible", False):
        return True
    return os.environ.get(ACCESSIBLE_ENV, "").strip().lower() in ("1", "true", "yes", "on")


def say(lines, stream=sys.stdout) -> None:
    if isinstance(lines, str):
        lines = [lines]
    for line in lines:
        print(line, file=stream)


# --------------------------------------------------------------------- loading
def load_program(path: str) -> tuple[bytes, Program | None]:
    p = Path(path)
    if p.suffix.lower() in (".bin", ".rom"):
        return p.read_bytes(), None
    prog = assemble(p.read_text(encoding="utf-8"))
    return prog.binary(), prog


class Console:
    """Character output device on a ROM I/O port.

    The 4004 can only write 4 bits at a time, so a character is sent as two
    WRR writes to the console port: high nibble first, then low nibble.

    In accessible mode output is line-buffered and each line is announced
    as "Output: ..." so it is not interleaved with other messages.
    """

    def __init__(self, port: int, stream=None, accessible: bool = False):
        self.port = port
        self.stream = stream or sys.stdout
        self.accessible = accessible
        self.high: int | None = None
        self.text = ""
        self.pending = ""

    def write(self, port: int, value: int) -> None:
        if port != self.port:
            return
        if self.high is None:
            self.high = value
            return
        ch = chr((self.high << 4) | value)
        self.high = None
        self.text += ch
        if not self.accessible:
            self.stream.write(ch)
            self.stream.flush()
        elif ch == "\n":
            self.flush()
        elif ch != "\r":
            self.pending += ch

    def flush(self) -> None:
        """Finish any partial line."""
        if self.accessible:
            if self.pending:
                self.stream.write(f"Output: {self.pending}\n")
            self.pending = ""
        elif self.text and not self.text.endswith("\n"):
            self.stream.write("\n")
        self.stream.flush()


def make_cpu(image: bytes, args) -> tuple[Intel4004, Console | None]:
    cpu = Intel4004()
    cpu.load(image)
    cpu.test = args.test
    for spec in args.rom_in or []:
        port, _, value = spec.partition("=")
        cpu.rom_inputs[int(port, 0) & 0xF] = int(value, 0) & 0xF
    console = None
    if args.console is not None:
        console = Console(args.console, accessible=is_accessible(args))
        cpu.on_rom_write = console.write
    return cpu, console


# -------------------------------------------------------------------- display
def fmt_state(cpu: Intel4004) -> str:
    regs = " ".join(f"{cpu.regs[i]:X}{cpu.regs[i + 1]:X}" for i in range(0, 16, 2))
    stack = " ".join(f"{a:03X}" for a in cpu.return_stack)
    return (f"PC={cpu.pc:03X} ACC={cpu.acc:X} CY={cpu.carry} "
            f"P0-P7=[{regs}] SRC={cpu.src:02X} DCL={cpu.bank} STK=[{stack}]")


def trace_line(cpu: Intel4004) -> None:
    text, size = disassemble(cpu.rom, cpu.pc)
    raw = " ".join(f"{cpu.rom[(cpu.pc + i) & 0xFFF]:02X}" for i in range(size))
    print(f"{cpu.pc:03X}  {raw:<5}  {text:<16} ACC={cpu.acc:X} CY={cpu.carry} "
          f"R={''.join(f'{r:X}' for r in cpu.regs)}", file=sys.stderr)


def dump_ram(cpu: Intel4004, show_all: bool = False) -> str:
    out = []
    for b in range(len(cpu.ram)):
        for c in range(4):
            for r in range(4):
                chars = cpu.ram[b][c][r]
                stat = cpu.status[b][c][r]
                if show_all or any(chars) or any(stat):
                    out.append(f"  bank {b} chip {c} reg {r}: "
                               f"{''.join(f'{x:X}' for x in chars)}  "
                               f"status {''.join(f'{x:X}' for x in stat)}")
    return "\n".join(out) or "  (all zero)"


def summary(cpu: Intel4004, reason: str) -> str:
    lines = [
        f"-- stopped: {reason} after {cpu.cycles} instruction cycles "
        f"({cpu.elapsed_us / 1000:.3f} ms at 740 kHz)",
        fmt_state(cpu),
        "RAM (non-zero registers):",
        dump_ram(cpu),
    ]
    rom_ports = [f"{i}:{v:X}" for i, v in enumerate(cpu.rom_ports) if v]
    ram_ports = [f"b{b}c{c}:{v:X}" for b, chips in enumerate(cpu.ram_ports)
                 for c, v in enumerate(chips) if v]
    lines.append(f"ROM ports: {' '.join(rom_ports) or '(all zero)'}   "
                 f"RAM ports: {' '.join(ram_ports) or '(all zero)'}")
    return "\n".join(lines)


# ------------------------------------------------------------------- commands
def cmd_asm(args) -> int:
    acc = is_accessible(args)
    src = Path(args.source)
    prog = assemble(src.read_text(encoding="utf-8"))
    out = Path(args.output) if args.output else src.with_suffix(".bin")
    out.write_bytes(prog.binary())
    if acc:
        say(f"Assembled {src.name}: {len(prog.binary())} bytes written to {out}.")
    else:
        print(f"wrote {len(prog.binary())} bytes to {out}")
    if args.listing and acc:
        say("Listing. Each line gives the source line number, address, bytes "
            "and source.")
        for ln in prog.lines:
            if not ln.data:
                if ln.label:
                    say(f"Line {ln.number}: label {ln.label} at {describe.hexaddr(ln.address)}.")
                continue
            hexes = " ".join(f"{b:02X}" for b in ln.data)
            code = " ".join(ln.text.split(";")[0].split())
            say(f"Line {ln.number}, {describe.hexaddr(ln.address)}, bytes {hexes}: {code}")
        say("Symbols.")
        for name, value in sorted(prog.symbols.items(), key=lambda kv: kv[1]):
            say(f"{name} is {describe.hexaddr(value)}, decimal {value}.")
    elif args.listing:
        print(prog.listing())
        print("\nSymbols:")
        for name, value in sorted(prog.symbols.items(), key=lambda kv: kv[1]):
            print(f"  {name:<16} 0x{value:03X}")
    return 0


def cmd_run(args) -> int:
    acc = is_accessible(args)
    image, _ = load_program(args.program)
    cpu, console = make_cpu(image, args)
    if acc and args.trace:
        narrator = describe.StepNarrator(lambda s: say(s, sys.stderr))
        start = cpu.cycles
        while not cpu.halted and cpu.cycles - start < args.max_cycles:
            narrator.step(cpu)
        reason = "halted" if cpu.halted else "cycle limit"
    else:
        reason = cpu.run(max_cycles=args.max_cycles,
                         trace=trace_line if args.trace else None)
    if console:
        console.flush()
    if not args.quiet:
        if acc:
            say(describe.summary_lines(cpu, reason), sys.stderr)
        else:
            print(summary(cpu, reason), file=sys.stderr)
    return 0


def cmd_disasm(args) -> int:
    acc = is_accessible(args)
    image, _ = load_program(args.program)
    cpu = Intel4004()
    cpu.load(image)
    addr = 0
    while addr < len(image):
        text, size = disassemble(cpu.rom, addr)
        raw = " ".join(f"{cpu.rom[addr + i]:02X}" for i in range(size))
        if acc:
            say(f"{describe.hexaddr(addr)}, bytes {raw}: "
                f"{describe.instruction_text(cpu.rom, addr)}")
        else:
            print(f"{addr:03X}  {raw:<5}  {text}")
        addr += size
    return 0


DEBUG_HELP = """commands:
  s [n]          step n instructions (default 1)
  c              continue until breakpoint / halt
  b <addr|label> toggle breakpoint        bl  list breakpoints
  r              show registers           m [all]  show RAM
  u [addr] [n]   disassemble              p  show I/O ports
  test 0|1       set TEST pin             in <port> <value>  set ROM input
  reset          reset CPU                q  quit"""

DEBUG_HELP_ACCESSIBLE = [
    "Debugger commands:",
    "s, optionally followed by a count: step one or more instructions.",
    "c: continue until a breakpoint or halt.",
    "b followed by an address or label: set or clear a breakpoint.",
    "bl: list breakpoints.",
    "r: describe the registers.",
    "m: describe RAM. m all includes empty registers.",
    "u, optionally followed by an address and a count: disassemble.",
    "p: describe the I/O ports.",
    "test 0 or test 1: set the test pin.",
    "in followed by a port and a value: set a ROM input port.",
    "reset: reset the CPU.",
    "q: quit.",
]


def cmd_debug(args) -> int:
    acc = is_accessible(args)
    image, prog = load_program(args.program)
    cpu, console = make_cpu(image, args)
    symbols = prog.symbols if prog else {}
    by_addr = {}
    for name, value in symbols.items():
        by_addr.setdefault(value, name)
    breaks: set[int] = set()
    narrator = describe.StepNarrator(say)

    def addr_of(token: str) -> int:
        return symbols[token.upper()] if token.upper() in symbols else int(token, 16)

    def label_at(a: int) -> str:
        return by_addr.get(a, "")

    def show_state():
        if acc:
            say(describe.state_lines(cpu))
        else:
            print(fmt_state(cpu))

    def show_next():
        if acc:
            label = f", label {label_at(cpu.pc)}" if label_at(cpu.pc) else ""
            halted = " The program has halted." if cpu.halted else ""
            say(f"Next instruction at {describe.hexaddr(cpu.pc)}{label}: "
                f"{describe.instruction_text(cpu.rom, cpu.pc)}{halted}")
        else:
            text, _ = disassemble(cpu.rom, cpu.pc)
            label = f"{label_at(cpu.pc)}: " if label_at(cpu.pc) else ""
            print(f"  next {cpu.pc:03X}  {label}{text}")

    if acc:
        say("Intel 4004 debugger. Type h for help.")
    else:
        print("Intel 4004 debugger -- type 'h' for help")
    show_state()
    show_next()
    while True:
        try:
            line = input("4004> ")
        except EOFError:
            print()
            break
        parts = shlex.split(line)
        if not parts:
            continue
        cmd, rest = parts[0].lower(), parts[1:]
        try:
            if cmd in ("q", "quit", "exit"):
                break
            elif cmd in ("h", "help", "?"):
                say(DEBUG_HELP_ACCESSIBLE if acc else DEBUG_HELP)
            elif cmd == "s":
                count = int(rest[0]) if rest else 1
                for _ in range(count):
                    if cpu.halted:
                        break
                    if acc and count == 1:
                        narrator.step(cpu)
                        continue
                    if not rest:
                        trace_line(cpu)
                    cpu.step()
                if acc and count > 1:
                    show_state()
                elif not acc:
                    print(fmt_state(cpu) + ("  [halted]" if cpu.halted else ""))
                show_next()
            elif cmd == "c":
                reason = cpu.run(max_cycles=args.max_cycles, breakpoints=breaks)
                if console and acc:
                    console.flush()
                if acc:
                    say(f"Stopped: {describe.STOP_REASONS.get(reason, reason)}.")
                else:
                    print(f"\n-- {reason}")
                show_state()
                show_next()
            elif cmd == "b":
                a = addr_of(rest[0])
                breaks ^= {a}
                state = "set" if a in breaks else "cleared"
                say(f"Breakpoint {state} at {describe.hexaddr(a)}." if acc
                    else f"breakpoint {state} at {a:03X}")
            elif cmd == "bl":
                if acc:
                    say("Breakpoints: " + (", ".join(describe.hexaddr(a) for a in sorted(breaks))
                                           or "none") + ".")
                else:
                    print(" ".join(f"{a:03X}" for a in sorted(breaks)) or "(none)")
            elif cmd == "r":
                show_state()
            elif cmd == "m":
                say(describe.ram_lines(cpu, bool(rest)) if acc
                    else dump_ram(cpu, show_all=bool(rest)))
            elif cmd == "p":
                if acc:
                    say(describe.port_lines(cpu))
                    inputs = [f"port {i} is {v}" for i, v in enumerate(cpu.rom_inputs) if v]
                    say("ROM input ports: " + (", ".join(inputs) + "." if inputs
                                               else "all zero."))
                else:
                    print("ROM out:", " ".join(f"{v:X}" for v in cpu.rom_ports))
                    print("ROM in: ", " ".join(f"{v:X}" for v in cpu.rom_inputs))
                    for b, chips in enumerate(cpu.ram_ports):
                        if any(chips):
                            print(f"RAM bank {b} ports:", " ".join(f"{v:X}" for v in chips))
            elif cmd == "u":
                a = addr_of(rest[0]) if rest else cpu.pc
                for _ in range(int(rest[1]) if len(rest) > 1 else 10):
                    text, size = disassemble(cpu.rom, a)
                    if acc:
                        label = f", label {label_at(a)}" if label_at(a) else ""
                        here = ", current position" if a == cpu.pc else ""
                        say(f"{describe.hexaddr(a)}{label}{here}: "
                            f"{describe.instruction_text(cpu.rom, a)}")
                    else:
                        label = f"{label_at(a)}:" if label_at(a) else ""
                        print(f"{'>' if a == cpu.pc else ' '}{a:03X}  {label:<10} {text}")
                    a = (a + size) & 0xFFF
            elif cmd == "test":
                cpu.test = int(rest[0]) & 1
                cpu.halted = False
                if acc:
                    say(f"Test pin set to {cpu.test}.")
            elif cmd == "in":
                port, value = int(rest[0], 0) & 0xF, int(rest[1], 0) & 0xF
                cpu.rom_inputs[port] = value
                if acc:
                    say(f"ROM input port {port} set to {value}.")
            elif cmd == "reset":
                cpu.reset()
                if acc:
                    say("CPU reset.")
                show_next()
            else:
                say("Unknown command. Type h for help." if acc
                    else "unknown command; 'h' for help")
        except (IndexError, ValueError, KeyError) as e:
            say(f"Bad arguments: {e}." if acc else f"bad arguments: {e}")
        except CPUError as e:
            say(f"CPU error: {e}")
    if console:
        console.flush()
    return 0


def main(argv=None) -> int:
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument(
        "-a", "--accessible", action="store_true", default=argparse.SUPPRESS,
        help="screen-reader friendly output: plain sentences, one fact per "
             f"line, no packed hex dumps (or set {ACCESSIBLE_ENV}=1)")

    ap = argparse.ArgumentParser(
        prog="i4004", parents=[common],
        description="Intel 4004 assembler, emulator and debugger")
    sub = ap.add_subparsers(dest="command", required=True)

    a = sub.add_parser("asm", parents=[common], help="assemble a .asm file to a ROM image")
    a.add_argument("source")
    a.add_argument("-o", "--output", help="output .bin (default: alongside source)")
    a.add_argument("-l", "--listing", action="store_true", help="print a listing")
    a.set_defaults(func=cmd_asm)

    d = sub.add_parser("disasm", parents=[common], help="disassemble a .bin or .asm")
    d.add_argument("program")
    d.set_defaults(func=cmd_disasm)

    for name, func, hlp in (("run", cmd_run, "run a program (.asm or .bin)"),
                            ("debug", cmd_debug, "interactive debugger")):
        p = sub.add_parser(name, parents=[common], help=hlp)
        p.add_argument("program")
        p.add_argument("--test", type=int, default=0, choices=(0, 1),
                       help="level of the TEST input pin")
        p.add_argument("--rom-in", action="append", metavar="PORT=VAL",
                       help="value returned by RDR for a ROM port (repeatable)")
        p.add_argument("--console", type=int, metavar="PORT", nargs="?", const=0,
                       help="treat ROM port PORT (default 0) as a character "
                            "terminal: two WRR writes = one char, high nibble first")
        p.add_argument("--max-cycles", type=int, default=10_000_000)
        if name == "run":
            p.add_argument("-t", "--trace", action="store_true",
                           help="print every instruction to stderr")
            p.add_argument("-q", "--quiet", action="store_true",
                           help="no final register/RAM dump")
        p.set_defaults(func=func)

    args = ap.parse_args(argv)
    try:
        return args.func(args)
    except (AsmError, CPUError, OSError) as e:
        print(f"Error: {e}" if is_accessible(args) else f"error: {e}", file=sys.stderr)
        return 1
