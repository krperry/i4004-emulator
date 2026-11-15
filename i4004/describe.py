"""Plain-language descriptions for accessible output.

Accessible mode is designed to read well with a screen reader:

* one fact per line, as full sentences;
* no bracketed dumps or packed hex strings;
* 4-bit values in decimal, addresses as "hex 039";
* runs of zeros are summarised instead of read out one by one;
* trace output says what each instruction does and what changed.
"""

from __future__ import annotations

from .cpu import Intel4004, disassemble

IMPLIED_TEXT = {
    "NOP": "no operation",
    "WRM": "write the accumulator to the selected RAM character",
    "WMP": "write the accumulator to the selected RAM output port",
    "WRR": "write the accumulator to the selected ROM output port",
    "WPM": "write program RAM, which this emulator ignores",
    "SBM": "subtract the selected RAM character and the borrow from the accumulator",
    "RDM": "read the selected RAM character into the accumulator",
    "RDR": "read the selected ROM input port into the accumulator",
    "ADM": "add the selected RAM character and the carry to the accumulator",
    "CLB": "clear the accumulator and the carry",
    "CLC": "clear the carry",
    "IAC": "add 1 to the accumulator",
    "CMC": "complement the carry",
    "CMA": "complement the accumulator",
    "RAL": "rotate the accumulator left through the carry",
    "RAR": "rotate the accumulator right through the carry",
    "TCC": "move the carry into the accumulator and clear the carry",
    "DAC": "subtract 1 from the accumulator",
    "TCS": "set the accumulator to 9, or 10 if carry is set, and clear the carry",
    "STC": "set the carry to 1",
    "DAA": "decimal adjust the accumulator",
    "KBP": "convert a single keyboard bit in the accumulator to a key number",
    "DCL": "select the RAM bank given by the accumulator",
}
for _n in range(4):
    IMPLIED_TEXT[f"WR{_n}"] = f"write the accumulator to status character {_n}"
    IMPLIED_TEXT[f"RD{_n}"] = f"read status character {_n} into the accumulator"


def hexaddr(value: int) -> str:
    return f"hex {value:03X}"


def condition_text(cond: int) -> str:
    plain = ["the accumulator is zero", "the carry is 1", "the test pin is 0"]
    negated = ["the accumulator is not zero", "the carry is 0", "the test pin is 1"]
    bits = [cond & 4, cond & 2, cond & 1]
    if cond & 8:
        parts = [n for b, n in zip(bits, negated) if b]
        return "always" if not parts else "if " + " and ".join(parts)
    parts = [p for b, p in zip(bits, plain) if b]
    return "never" if not parts else "if " + " or ".join(parts)


def spoken_mnemonic(mem, addr: int) -> tuple[str, int]:
    text, size = disassemble(mem, addr)
    return text.replace(",", ", ").replace("0x", "hex "), size


def instruction_text(mem, addr: int) -> str:
    """'LDM 5. Load 5 into the accumulator.' style description."""
    op = mem[addr & 0xFFF]
    nxt = mem[(addr + 1) & 0xFFF]
    hi, lo = op >> 4, op & 0xF
    page_target = ((addr + 2) & 0xF00) | nxt
    mnemonic, _ = spoken_mnemonic(mem, addr)
    p = lo >> 1
    if hi == 0x1:
        cond = condition_text(lo)
        if cond == "always":
            what = f"jump to {hexaddr(page_target)}"
        elif cond == "never":
            what = "never jumps, condition is 0"
        else:
            what = f"jump to {hexaddr(page_target)} {cond}"
    elif hi == 0x2 and lo % 2 == 0:
        what = f"load hex {nxt:02X} into register pair {p}, registers {2*p} and {2*p+1}"
    elif hi == 0x2:
        what = f"send register pair {p} as the RAM and ROM port address"
    elif hi == 0x3 and lo % 2 == 0:
        what = f"fetch the ROM byte addressed by register pair 0 into register pair {p}"
    elif hi == 0x3:
        what = f"jump within this page to the address in register pair {p}"
    elif hi == 0x4:
        what = f"jump to {hexaddr(((lo << 8) | nxt))}"
    elif hi == 0x5:
        what = f"call the subroutine at {hexaddr(((lo << 8) | nxt))}"
    elif hi == 0x6:
        what = f"add 1 to register {lo}"
    elif hi == 0x7:
        what = (f"add 1 to register {lo}, then jump to {hexaddr(page_target)} "
                f"unless it became 0")
    elif hi == 0x8:
        what = f"add register {lo} and the carry to the accumulator"
    elif hi == 0x9:
        what = f"subtract register {lo} and the borrow from the accumulator"
    elif hi == 0xA:
        what = f"copy register {lo} into the accumulator"
    elif hi == 0xB:
        what = f"swap the accumulator with register {lo}"
    elif hi == 0xC:
        what = f"return from subroutine with {lo} in the accumulator"
    elif hi == 0xD:
        what = f"load {lo} into the accumulator"
    else:
        what = IMPLIED_TEXT.get(mnemonic, "unused opcode, treated as data")
    return f"{mnemonic}. {what[0].upper()}{what[1:]}."


# ------------------------------------------------------------------ values
def list_values(values) -> str:
    """'4, 4, 7, 8, then 12 zeros' -- trailing zeros summarised."""
    values = list(values)
    end = len(values)
    while end and values[end - 1] == 0:
        end -= 1
    if end == 0:
        return f"all {len(values)} are 0"
    shown = ", ".join(str(v) for v in values[:end])
    zeros = len(values) - end
    if zeros == 0:
        return shown
    return f"{shown}, then {zeros} zero{'s' if zeros > 1 else ''}"


def registers_text(cpu: Intel4004) -> str:
    nonzero = [f"R{i} is {v}" for i, v in enumerate(cpu.regs) if v]
    if not nonzero:
        return "Registers: all 16 are 0."
    rest = " All others are 0." if len(nonzero) < 16 else ""
    return "Registers: " + ", ".join(nonzero) + "." + rest


def src_text(cpu: Intel4004) -> str:
    s = cpu.src
    return (f"Selected address from SRC: hex {s:02X}, which is RAM chip {s >> 6}, "
            f"register {(s >> 4) & 3}, character {s & 0xF}, or ROM port {s >> 4}.")


def state_lines(cpu: Intel4004) -> list[str]:
    stack = ", ".join(hexaddr(a) for a in cpu.return_stack)
    return [
        f"Program counter: {hexaddr(cpu.pc)}.",
        f"Accumulator: {cpu.acc}. Carry: {cpu.carry}.",
        registers_text(cpu),
        f"Return stack, most recent first: {stack}.",
        src_text(cpu),
        f"Selected RAM bank: {cpu.bank}.",
    ]


def ram_lines(cpu: Intel4004, show_all: bool = False) -> list[str]:
    out = []
    for b, chips in enumerate(cpu.ram):
        for c in range(4):
            for r in range(4):
                chars = chips[c][r]
                stat = cpu.status[b][c][r]
                if not (show_all or any(chars) or any(stat)):
                    continue
                line = f"RAM bank {b}, chip {c}, register {r}. Characters: {list_values(chars)}."
                if any(stat):
                    line += f" Status: {list_values(stat)}."
                out.append(line)
    return out or ["RAM: all zero."]


def port_lines(cpu: Intel4004) -> list[str]:
    rom = [f"port {i} is {v}" for i, v in enumerate(cpu.rom_ports) if v]
    ram = [f"bank {b} chip {c} is {v}" for b, chips in enumerate(cpu.ram_ports)
           for c, v in enumerate(chips) if v]
    return [
        "ROM output ports: " + (", ".join(rom) + "." if rom else "all zero."),
        "RAM output ports: " + (", ".join(ram) + "." if ram else "all zero."),
    ]


STOP_REASONS = {
    "halted": "the program reached a jump to itself, which this emulator "
              "treats as a halt, because the 4004 has no halt instruction",
    "cycle limit": "the cycle limit was reached",
    "breakpoint": "a breakpoint was reached",
}


def summary_lines(cpu: Intel4004, reason: str) -> list[str]:
    ms = cpu.elapsed_us / 1000
    return [
        f"Stopped at {hexaddr(cpu.pc)}: {STOP_REASONS.get(reason, reason)}.",
        f"Ran {cpu.cycles} instruction cycles, which is {ms:.3f} milliseconds "
        f"on a real 4004 at 740 kilohertz.",
        *state_lines(cpu),
        *ram_lines(cpu),
        *port_lines(cpu),
    ]


# ------------------------------------------------------------------- trace
class StepNarrator:
    """Describes each instruction and the state changes it caused."""

    def __init__(self, emit):
        self.emit = emit
        self.count = 0

    def snapshot(self, cpu: Intel4004) -> dict:
        b, c, r = cpu.bank, cpu.src >> 6, (cpu.src >> 4) & 3
        return {
            "acc": cpu.acc, "carry": cpu.carry, "regs": list(cpu.regs),
            "sp": cpu.sp, "src": cpu.src, "bank": cpu.bank,
            "sel": (b, c, r),
            "chars": list(cpu.ram[b][c][r]), "status": list(cpu.status[b][c][r]),
            "rom_ports": list(cpu.rom_ports),
            "ram_ports": [list(x) for x in cpu.ram_ports],
        }

    def step(self, cpu: Intel4004) -> None:
        addr = cpu.pc
        text = instruction_text(cpu.rom, addr)
        _, size = disassemble(cpu.rom, addr)
        mnemonic = text.split(".")[0].split()[0]
        before = self.snapshot(cpu)
        cpu.step()
        self.count += 1
        changes = self.changes(cpu, before, addr, size, mnemonic)
        self.emit(f"Step {self.count}, at {hexaddr(addr)}: {text} "
                  f"{'Result: ' + ', '.join(changes) + '.' if changes else 'No change.'}")

    def changes(self, cpu, before, addr, size, mnemonic) -> list[str]:
        out = []
        if cpu.acc != before["acc"]:
            out.append(f"accumulator now {cpu.acc}")
        if cpu.carry != before["carry"]:
            out.append(f"carry now {cpu.carry}")
        for i, (old, new) in enumerate(zip(before["regs"], cpu.regs)):
            if old != new:
                out.append(f"R{i} now {new}")
        if cpu.src != before["src"]:
            out.append(f"selected address now hex {cpu.src:02X}")
        if cpu.bank != before["bank"]:
            out.append(f"RAM bank now {cpu.bank}")
        b, c, r = before["sel"]
        for i, (old, new) in enumerate(zip(before["chars"], cpu.ram[b][c][r])):
            if old != new or (mnemonic == "WRM" and i == cpu.src & 0xF):
                out.append(f"wrote {new} to RAM bank {b}, chip {c}, register {r}, "
                           f"character {i}")
        for i, (old, new) in enumerate(zip(before["status"], cpu.status[b][c][r])):
            if old != new or (mnemonic == f"WR{i}"):
                out.append(f"wrote {new} to status character {i} of RAM bank {b}, "
                           f"chip {c}, register {r}")
        if mnemonic == "WRR":
            out.append(f"wrote {cpu.acc} to ROM port {cpu.src >> 4}")
        if mnemonic == "WMP":
            out.append(f"wrote {cpu.acc} to RAM output port, bank {cpu.bank}, "
                       f"chip {cpu.src >> 6}")
        if cpu.sp != before["sp"]:
            out.append("called a subroutine" if mnemonic == "JMS" else "returned")
        next_pc = (addr + size) & 0xFFF
        if cpu.halted:
            out.append("jumped to itself, so the program has halted")
        elif cpu.pc != next_pc:
            out.append(f"jumped to {hexaddr(cpu.pc)}")
        elif mnemonic in ("JCN", "ISZ"):
            out.append("did not jump")
        return out
