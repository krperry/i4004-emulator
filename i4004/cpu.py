"""Intel 4004 CPU core plus a minimal MCS-4 system (4001 ROM, 4002 RAM).

Behaviour follows the Intel MCS-4 User's Manual / 4004 data sheet:

* 4-bit accumulator, 1-bit carry, sixteen 4-bit index registers
  (eight register pairs), 12-bit program counter.
* Program counter lives in a 4 x 12-bit address stack: the current PC
  plus three return levels.  A fourth nested JMS silently overwrites the
  oldest return address, exactly like the real chip.
* 46 instructions.  One-byte instructions take one instruction cycle
  (8 clock periods, 10.8 us at 740 kHz); two-byte instructions and FIN
  take two.
* JCN / ISZ / FIN / JIN work inside the current ROM page.  "Current" is
  the page of the PC *after* the instruction has been fetched, so an
  instruction sitting at the end of a page reaches into the next page --
  the documented 4004 page-boundary quirk.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Optional

CLOCK_HZ = 740_000
CLOCKS_PER_CYCLE = 8
ROM_SIZE = 4096
NUM_BANKS = 8        # DCL selects 0-7 (assumes a 3-to-8 CM-RAM decoder)
CHIPS_PER_BANK = 4   # 4002 chips per bank
REGS_PER_CHIP = 4
CHARS_PER_REG = 16
STATUS_PER_REG = 4

# KBP: keyboard process, converts a one-hot code to a key number.
KBP_TABLE = {0: 0, 1: 1, 2: 2, 4: 3, 8: 4}

ONE_BYTE = {
    0xE0: "WRM", 0xE1: "WMP", 0xE2: "WRR", 0xE3: "WPM",
    0xE4: "WR0", 0xE5: "WR1", 0xE6: "WR2", 0xE7: "WR3",
    0xE8: "SBM", 0xE9: "RDM", 0xEA: "RDR", 0xEB: "ADM",
    0xEC: "RD0", 0xED: "RD1", 0xEE: "RD2", 0xEF: "RD3",
    0xF0: "CLB", 0xF1: "CLC", 0xF2: "IAC", 0xF3: "CMC",
    0xF4: "CMA", 0xF5: "RAL", 0xF6: "RAR", 0xF7: "TCC",
    0xF8: "DAC", 0xF9: "TCS", 0xFA: "STC", 0xFB: "DAA",
    0xFC: "KBP", 0xFD: "DCL",
}


class CPUError(Exception):
    pass


def disassemble(mem, addr: int) -> tuple[str, int]:
    """Return (text, size) for the instruction at ``addr``."""
    op = mem[addr & 0xFFF]
    nxt = mem[(addr + 1) & 0xFFF]
    hi, lo = op >> 4, op & 0xF
    if op == 0x00:
        return "NOP", 1
    if hi == 0x1:
        target = ((addr + 2) & 0xF00) | nxt
        return f"JCN {lo},0x{target:03X}", 2
    if hi == 0x2:
        return (f"FIM P{lo >> 1},0x{nxt:02X}", 2) if lo % 2 == 0 \
            else (f"SRC P{lo >> 1}", 1)
    if hi == 0x3:
        return (f"FIN P{lo >> 1}", 1) if lo % 2 == 0 else (f"JIN P{lo >> 1}", 1)
    if hi == 0x4:
        return f"JUN 0x{(lo << 8) | nxt:03X}", 2
    if hi == 0x5:
        return f"JMS 0x{(lo << 8) | nxt:03X}", 2
    if hi == 0x6:
        return f"INC R{lo}", 1
    if hi == 0x7:
        target = ((addr + 2) & 0xF00) | nxt
        return f"ISZ R{lo},0x{target:03X}", 2
    if hi <= 0xB:
        return f"{['ADD', 'SUB', 'LD', 'XCH'][hi - 8]} R{lo}", 1
    if hi == 0xC:
        return f"BBL {lo}", 1
    if hi == 0xD:
        return f"LDM {lo}", 1
    if op in ONE_BYTE:
        return ONE_BYTE[op], 1
    return f"DB 0x{op:02X}", 1  # FE, FF are unused opcodes


@dataclass
class Intel4004:
    rom: bytearray = field(default_factory=lambda: bytearray(ROM_SIZE))

    # Optional I/O hooks.  Defaults just store/return values.
    on_rom_write: Optional[Callable[[int, int], None]] = None      # (port, value)
    on_rom_read: Optional[Callable[[int], int]] = None              # (port) -> value
    on_ram_port_write: Optional[Callable[[int, int, int], None]] = None  # (bank, chip, value)

    def __post_init__(self):
        if len(self.rom) < ROM_SIZE:
            self.rom = bytearray(self.rom) + bytearray(ROM_SIZE - len(self.rom))
        self.test = 0                      # TEST input pin
        self.rom_inputs = [0] * 16         # values read by RDR, per ROM chip
        self.reset()

    # ------------------------------------------------------------------ state
    def reset(self) -> None:
        self.acc = 0
        self.carry = 0
        self.regs = [0] * 16
        self.stack = [0] * 4               # stack[sp] is the PC
        self.sp = 0
        self.src = 0                       # last SRC address (8 bits)
        self.bank = 0                      # last DCL value
        self.cycles = 0
        self.halted = False
        self.ram = [[[[0] * CHARS_PER_REG for _ in range(REGS_PER_CHIP)]
                     for _ in range(CHIPS_PER_BANK)] for _ in range(NUM_BANKS)]
        self.status = [[[[0] * STATUS_PER_REG for _ in range(REGS_PER_CHIP)]
                        for _ in range(CHIPS_PER_BANK)] for _ in range(NUM_BANKS)]
        self.ram_ports = [[0] * CHIPS_PER_BANK for _ in range(NUM_BANKS)]
        self.rom_ports = [0] * 16

    def load(self, image: bytes, origin: int = 0) -> None:
        for i, b in enumerate(image):
            self.rom[(origin + i) & 0xFFF] = b

    @property
    def pc(self) -> int:
        return self.stack[self.sp]

    @pc.setter
    def pc(self, value: int) -> None:
        self.stack[self.sp] = value & 0xFFF

    @property
    def return_stack(self) -> list[int]:
        """Return addresses, most recent first."""
        return [self.stack[(self.sp - i) % 4] for i in range(1, 4)]

    @property
    def elapsed_us(self) -> float:
        return self.cycles * CLOCKS_PER_CYCLE * 1e6 / CLOCK_HZ

    def pair(self, p: int) -> int:
        return (self.regs[2 * p] << 4) | self.regs[2 * p + 1]

    def set_pair(self, p: int, value: int) -> None:
        self.regs[2 * p] = (value >> 4) & 0xF
        self.regs[2 * p + 1] = value & 0xF

    # SRC address decoding: for RAM, bits 7-6 chip, 5-4 register,
    # 3-0 character.  For ROM I/O, bits 7-4 select the ROM chip/port.
    def _ram_sel(self):
        return self.bank, self.src >> 6, (self.src >> 4) & 3, self.src & 0xF

    def _ram_read(self) -> int:
        b, c, r, ch = self._ram_sel()
        return self.ram[b][c][r][ch]

    # ------------------------------------------------------------- execution
    def _fetch(self) -> int:
        byte = self.rom[self.pc]
        self.pc = self.pc + 1
        return byte

    def _add(self, value: int, carry_in: int) -> None:
        s = self.acc + value + carry_in
        self.acc = s & 0xF
        self.carry = s >> 4

    def step(self) -> None:
        """Execute one instruction."""
        addr = self.pc
        op = self._fetch()
        hi, lo = op >> 4, op & 0xF
        self.cycles += 1

        if op == 0x00:                                   # NOP
            return

        if hi in (0x1, 0x4, 0x5, 0x7) or (hi == 0x2 and lo % 2 == 0):
            arg = self._fetch()                          # second byte
            self.cycles += 1
            page = self.pc & 0xF00
            if hi == 0x1:                                # JCN
                jump = ((lo & 4 and self.acc == 0) or
                        (lo & 2 and self.carry == 1) or
                        (lo & 1 and self.test == 0))
                if lo & 8:
                    jump = not jump
                if jump:
                    self.pc = page | arg
                    if self.pc == addr:                  # can never leave
                        self.halted = True
            elif hi == 0x2:                              # FIM
                self.set_pair(lo >> 1, arg)
            elif hi == 0x4:                              # JUN
                self.pc = (lo << 8) | arg
                if self.pc == addr:
                    self.halted = True
            elif hi == 0x5:                              # JMS
                self.sp = (self.sp + 1) % 4
                self.pc = (lo << 8) | arg
            else:                                        # ISZ
                self.regs[lo] = (self.regs[lo] + 1) & 0xF
                if self.regs[lo]:
                    self.pc = page | arg
            return

        if hi == 0x2:                                    # SRC
            self.src = self.pair(lo >> 1)
        elif hi == 0x3:
            if lo % 2 == 0:                              # FIN
                self.cycles += 1
                self.set_pair(lo >> 1, self.rom[(self.pc & 0xF00) | self.pair(0)])
            else:                                        # JIN
                self.pc = (self.pc & 0xF00) | self.pair(lo >> 1)
        elif hi == 0x6:                                  # INC
            self.regs[lo] = (self.regs[lo] + 1) & 0xF
        elif hi == 0x8:                                  # ADD
            self._add(self.regs[lo], self.carry)
        elif hi == 0x9:                                  # SUB
            self._add(self.regs[lo] ^ 0xF, self.carry ^ 1)
        elif hi == 0xA:                                  # LD
            self.acc = self.regs[lo]
        elif hi == 0xB:                                  # XCH
            self.acc, self.regs[lo] = self.regs[lo], self.acc
        elif hi == 0xC:                                  # BBL
            self.sp = (self.sp - 1) % 4
            self.acc = lo
        elif hi == 0xD:                                  # LDM
            self.acc = lo
        elif hi == 0xE:
            self._io(lo)
        else:
            self._accumulator_group(op)

    def _io(self, lo: int) -> None:
        b, c, r, ch = self._ram_sel()
        if lo == 0x0:                                    # WRM
            self.ram[b][c][r][ch] = self.acc
        elif lo == 0x1:                                  # WMP
            self.ram_ports[b][c] = self.acc
            if self.on_ram_port_write:
                self.on_ram_port_write(b, c, self.acc)
        elif lo == 0x2:                                  # WRR
            port = self.src >> 4
            self.rom_ports[port] = self.acc
            if self.on_rom_write:
                self.on_rom_write(port, self.acc)
        elif lo == 0x3:                                  # WPM
            pass  # needs 4008/4009 program-RAM hardware; ignored
        elif lo <= 0x7:                                  # WR0-WR3
            self.status[b][c][r][lo - 4] = self.acc
        elif lo == 0x8:                                  # SBM
            self._add(self.ram[b][c][r][ch] ^ 0xF, self.carry ^ 1)
        elif lo == 0x9:                                  # RDM
            self.acc = self.ram[b][c][r][ch]
        elif lo == 0xA:                                  # RDR
            port = self.src >> 4
            value = self.on_rom_read(port) if self.on_rom_read else self.rom_inputs[port]
            self.acc = value & 0xF
        elif lo == 0xB:                                  # ADM
            self._add(self.ram[b][c][r][ch], self.carry)
        else:                                            # RD0-RD3
            self.acc = self.status[b][c][r][lo - 0xC]

    def _accumulator_group(self, op: int) -> None:
        if op == 0xF0:                                   # CLB
            self.acc = 0
            self.carry = 0
        elif op == 0xF1:                                 # CLC
            self.carry = 0
        elif op == 0xF2:                                 # IAC
            self._add(1, 0)
        elif op == 0xF3:                                 # CMC
            self.carry ^= 1
        elif op == 0xF4:                                 # CMA
            self.acc ^= 0xF
        elif op == 0xF5:                                 # RAL
            s = (self.acc << 1) | self.carry
            self.acc, self.carry = s & 0xF, s >> 4
        elif op == 0xF6:                                 # RAR
            self.acc, self.carry = (self.acc >> 1) | (self.carry << 3), self.acc & 1
        elif op == 0xF7:                                 # TCC
            self.acc = self.carry
            self.carry = 0
        elif op == 0xF8:                                 # DAC (carry=1: no borrow)
            self._add(0xF, 0)
        elif op == 0xF9:                                 # TCS
            self.acc = 10 if self.carry else 9
            self.carry = 0
        elif op == 0xFA:                                 # STC
            self.carry = 1
        elif op == 0xFB:                                 # DAA
            if self.acc > 9 or self.carry:
                s = self.acc + 6
                self.acc = s & 0xF
                if s > 0xF:
                    self.carry = 1                       # never cleared
        elif op == 0xFC:                                 # KBP
            self.acc = KBP_TABLE.get(self.acc, 0xF)
        elif op == 0xFD:                                 # DCL
            self.bank = self.acc & 0x7
        else:
            raise CPUError(f"illegal opcode 0x{op:02X} at 0x{(self.pc - 1) & 0xFFF:03X}")

    def run(self, max_cycles: int = 10_000_000, breakpoints=(), trace=None) -> str:
        """Run until halt, breakpoint or cycle limit.  Returns the reason."""
        start = self.cycles
        first = True
        while not self.halted:
            if not first and self.pc in breakpoints:
                return "breakpoint"
            first = False
            if self.cycles - start >= max_cycles:
                return "cycle limit"
            if trace:
                trace(self)
            self.step()
        return "halted"
