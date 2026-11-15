# i4004: an Intel 4004 emulator

The Intel 4004, the first commercial single-chip microprocessor, was
announced on 15 November 1971. This is a command-line emulator,
assembler, disassembler and debugger for it, written in pure Python 3.10+
with no dependencies.

## Quick start

```
python -m i4004 run examples/hello.asm --console
python -m i4004 run examples/bcd_add.asm --console
python -m i4004 run examples/bcd_add.asm --trace      # trace each step
python -m i4004 debug examples/bcd_add.asm --console  # debugger
python -m i4004 asm examples/hello.asm -l             # .bin + listing
python -m i4004 disasm examples/hello.bin
```

`run` and `debug` accept `.asm` source (assembled on the fly) or a raw
`.bin`/`.rom` image loaded at address 0.

Run options:

| Option | Meaning |
| --- | --- |
| `--console [PORT]` | ROM port (default 0) acts as a terminal: two `WRR` writes, high nibble first, make one character |
| `--test 0\|1` | level of the TEST input pin (read by `JCN`) |
| `--rom-in PORT=VAL` | value `RDR` returns for a ROM port (repeatable) |
| `--max-cycles N` | stop after N instruction cycles |
| `-t`, `--trace` | print every instruction to stderr |
| `-q`, `--quiet` | skip the final register and RAM dump |
| `-a`, `--accessible` | screen-reader friendly output (see below) |

## Accessible output

Add `-a` or `--accessible` to any command, before or after the command
name, or set the environment variable `I4004_ACCESSIBLE=1` to turn it on
permanently. Output then becomes plain sentences built for screen
readers:

- One fact per line, with no bracketed dumps or packed hex strings.
- 4-bit values are shown in decimal. Addresses are shown as `hex 039`.
- Trailing zeros in RAM are summarised ("then 8 zeros") instead of being
  read out one by one.
- Console output is line-buffered and announced as `Output: ...`, so it
  never interleaves mid-sentence with other messages.
- `--trace`, and single steps in the debugger, say what each instruction
  does and what it changed:

```
Step 6, at hex 007: JCN 12, hex 00C. Jump to hex 00C if the accumulator
is not zero. Result: jumped to hex 00C.
Step 8, at hex 00D: WRR. Write the accumulator to the selected ROM output
port. Result: wrote 4 to ROM port 0.
```

`asm -l` and `disasm` also produce a line-by-line spoken listing in this
mode.

The 4004 has no HALT instruction. The emulator stops when a program
jumps to itself (`done: JUN done`), the usual idiom, or when it reaches
the cycle limit.

## What is emulated

- **CPU:** 4-bit accumulator, carry, sixteen 4-bit registers (eight
  pairs), and the 12-bit PC in a 4-level address stack (PC plus three
  return addresses). A fourth nested `JMS` overwrites the oldest return
  address, as on the real chip.
- **All 46 instructions,** including the odd ones: `SUB`/`SBM` use carry
  as an inverted borrow, `DAA` never clears carry, plus `TCS`, `KBP` and
  `DAC`.
- **ROM page rules:** `JCN`, `ISZ`, `FIN` and `JIN` stay within the
  current 256-byte page. An instruction at the end of a page reaches into
  the next one, as the data sheet describes.
- **Timing:** 1 instruction cycle (8 clocks, 10.8 us at 740 kHz) per
  one-byte instruction, and 2 cycles for two-byte instructions and `FIN`.
- **4001 ROM:** 4 KB program space, plus a 4-bit I/O port per ROM chip
  (`WRR`/`RDR`).
- **4002 RAM:** 8 banks (selected by `DCL`), each with 4 chips x 4
  registers x (16 data + 4 status) characters, and a 4-bit output port per
  chip (`WMP`). The `SRC` address splits into chip (bits 7-6), register
  (bits 5-4) and character (bits 3-0). For ROM I/O, bits 7-4 select the
  port.
- `WPM` (program-RAM write) needs 4008/4009 hardware and is a no-op here.

## Assembly syntax

```
; comment
NAME    EQU  0x10           ; or  NAME = 0x10
        ORG  0
start:  FIM  P0, table      ; pairs: P0-P7, 0P-7P, R0R1..R14R15
        FIN  P1
        LD   R2             ; registers: R0-R15
        JCN  AZ, start      ; conditions: TZ TN C1 C0 AZ AN, or 0-15
        JNZ  start          ; pseudo-ops: JZ JNZ JC JNC JT JNT
        ISZ  R3, start
        JMS  sub
done:   JUN  done
sub:    BBL  0
table:  DB   1, 0x2F, 'A', "TEXT", 0
```

- Labels end with `:`, or with `,` in the original Intel style.
- Numbers can be written as `42`, `0x2A`, `$2A`, `2AH`, `0b101010`,
  `101010B` or `'A'`. A bare `$` is the current address.
- Expressions support `+ - * / % & | ^ << >> ~` and parentheses.
- The assembler rejects a `JCN`/`ISZ` target outside the current page.

## Debugger commands

`s [n]` step, `c` continue, `b <addr|label>` toggle a breakpoint, `r`
registers, `m` RAM, `u [addr] [n]` disassemble, `p` I/O ports,
`test 0|1` set the TEST pin, `in <port> <val>` set a ROM input, `reset`,
`q` quit.

## Library use

```python
from i4004 import Intel4004, assemble

cpu = Intel4004()
cpu.load(assemble(open("examples/hello.asm").read()).binary())
cpu.on_rom_write = lambda port, value: print(port, value)
cpu.run()
print(cpu.acc, cpu.carry, cpu.regs, cpu.cycles)
```

## Tests

```
python -m unittest discover -s tests
```
