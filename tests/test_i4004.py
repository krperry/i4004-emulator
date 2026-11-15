import io
import unittest
from pathlib import Path

from i4004 import AsmError, Intel4004, assemble, disassemble
from i4004.cli import Console

EXAMPLES = Path(__file__).resolve().parent.parent / "examples"


def run(src: str, test: int = 0, max_cycles: int = 100_000) -> Intel4004:
    cpu = Intel4004()
    cpu.load(assemble(src).binary())
    cpu.test = test
    reason = cpu.run(max_cycles=max_cycles)
    assert reason == "halted", reason
    return cpu


HALT = "\nhalt: JUN halt\n"


class Arithmetic(unittest.TestCase):
    def test_add_with_carry(self):
        cpu = run("LDM 9\nXCH R0\nLDM 8\nSTC\nADD R0" + HALT)
        self.assertEqual((cpu.acc, cpu.carry), (2, 1))   # 8+9+1 = 18

    def test_sub_no_borrow_sets_carry(self):
        cpu = run("LDM 3\nXCH R0\nLDM 5\nCLC\nSUB R0" + HALT)
        self.assertEqual((cpu.acc, cpu.carry), (2, 1))

    def test_sub_borrow_clears_carry(self):
        cpu = run("LDM 5\nXCH R0\nLDM 3\nCLC\nSUB R0" + HALT)
        self.assertEqual((cpu.acc, cpu.carry), (14, 0))  # -2 = 0xE, borrow

    def test_sub_borrow_in(self):
        cpu = run("LDM 3\nXCH R0\nLDM 5\nSTC\nSUB R0" + HALT)
        self.assertEqual((cpu.acc, cpu.carry), (1, 1))   # carry=1 means borrow in

    def test_iac_dac(self):
        self.assertEqual(run("LDM 15\nIAC" + HALT).carry, 1)
        cpu = run("LDM 0\nDAC" + HALT)
        self.assertEqual((cpu.acc, cpu.carry), (15, 0))
        cpu = run("LDM 5\nDAC" + HALT)
        self.assertEqual((cpu.acc, cpu.carry), (4, 1))

    def test_daa(self):
        cpu = run("LDM 12\nCLC\nDAA" + HALT)
        self.assertEqual((cpu.acc, cpu.carry), (2, 1))
        cpu = run("LDM 2\nSTC\nDAA" + HALT)              # carry never cleared
        self.assertEqual((cpu.acc, cpu.carry), (8, 1))
        cpu = run("LDM 7\nCLC\nDAA" + HALT)
        self.assertEqual((cpu.acc, cpu.carry), (7, 0))

    def test_tcs_tcc(self):
        self.assertEqual((run("STC\nTCS" + HALT).acc), 10)
        cpu = run("CLC\nTCS" + HALT)
        self.assertEqual((cpu.acc, cpu.carry), (9, 0))
        cpu = run("STC\nTCC" + HALT)
        self.assertEqual((cpu.acc, cpu.carry), (1, 0))

    def test_rotates(self):
        cpu = run("LDM 0b1001\nCLC\nRAL" + HALT)
        self.assertEqual((cpu.acc, cpu.carry), (0b0010, 1))
        cpu = run("LDM 0b1001\nSTC\nRAR" + HALT)
        self.assertEqual((cpu.acc, cpu.carry), (0b1100, 1))

    def test_cma_cmc_clb(self):
        cpu = run("LDM 5\nCMA\nCMC" + HALT)
        self.assertEqual((cpu.acc, cpu.carry), (10, 1))
        cpu = run("LDM 5\nSTC\nCLB" + HALT)
        self.assertEqual((cpu.acc, cpu.carry), (0, 0))

    def test_kbp(self):
        for value, expected in [(0, 0), (1, 1), (2, 2), (4, 3), (8, 4), (3, 15), (15, 15)]:
            self.assertEqual(run(f"LDM {value}\nKBP" + HALT).acc, expected)


class ControlFlow(unittest.TestCase):
    def test_jcn_conditions(self):
        src = """
            LDM 0
            JCN AZ, yes
            LDM 1
            JUN out
        yes: LDM 2
        out: XCH R0
            LDM 3
            JCN AN, yes2
            JUN halt
        yes2: LDM 9
            XCH R1
        """ + HALT
        cpu = run(src)
        self.assertEqual((cpu.regs[0], cpu.regs[1]), (2, 9))

    def test_jcn_test_pin(self):
        src = "JT low\nLDM 1\nJUN halt\nlow: LDM 2" + HALT
        self.assertEqual(run(src, test=0).acc, 2)
        self.assertEqual(run(src, test=1).acc, 1)

    def test_jcn_page_boundary(self):
        # JCN at 0x0FE: second byte at 0x0FF, so the jump lands on page 1.
        src = "ORG 0xFE\nJCN 8, 0x110\nORG 0x110\nLDM 7" + HALT
        cpu = Intel4004()
        cpu.load(assemble(src).image)
        cpu.pc = 0xFE
        cpu.step()
        self.assertEqual(cpu.pc, 0x110)

    def test_page_target_checked(self):
        with self.assertRaises(AsmError):
            assemble("JCN AZ, far\nORG 0x200\nfar: NOP")

    def test_isz_loop(self):
        cpu = run("LDM 0\nXCH R1\nLDM 11\nXCH R0\nlp: INC R1\nISZ R0, lp" + HALT)
        self.assertEqual(cpu.regs[1], 5)

    def test_subroutine_and_bbl(self):
        cpu = run("JMS sub\nXCH R0\nJUN halt\nsub: BBL 6" + HALT)
        self.assertEqual(cpu.regs[0], 6)

    def test_stack_overflow_overwrites_oldest(self):
        cpu = Intel4004()
        cpu.load(assemble("JMS 0x100\nORG 0x100\nJMS 0x200\nORG 0x200\n"
                          "JMS 0x300\nORG 0x300\nJMS 0x400").image)
        for _ in range(4):
            cpu.step()
        self.assertEqual(cpu.pc, 0x400)
        self.assertEqual(cpu.return_stack, [0x302, 0x202, 0x102])  # 0x002 lost

    def test_fin_and_jin(self):
        src = """
            FIM P0, table
            FIN P3
            FIM P1, target
            JIN P1
            LDM 1
        target: LDM 2
            JUN halt
        table: DB 0xA5
        """ + HALT
        cpu = run(src)
        self.assertEqual((cpu.regs[6], cpu.regs[7], cpu.acc), (0xA, 0x5, 2))

    def test_cycle_counts(self):
        cpu = run("NOP\nFIM P0, 0\nFIN P1" + HALT)
        self.assertEqual(cpu.cycles, 1 + 2 + 2 + 2)


class Memory(unittest.TestCase):
    def test_ram_and_status(self):
        src = """
            LDM 2
            DCL
            FIM P0, 0xB7      ; chip 2, reg 3, char 7
            SRC P0
            LDM 9
            WRM
            LDM 4
            WR2
            LDM 0
            RDM
            XCH R5
            RD2
            XCH R6
        """ + HALT
        cpu = run(src)
        self.assertEqual(cpu.ram[2][2][3][7], 9)
        self.assertEqual(cpu.status[2][2][3][2], 4)
        self.assertEqual((cpu.regs[5], cpu.regs[6]), (9, 4))

    def test_adm_sbm(self):
        src = "FIM P0,0\nSRC P0\nLDM 6\nWRM\nLDM 9\nCLC\nADM\nXCH R2\nLDM 9\nCLC\nSBM" + HALT
        cpu = run(src)
        self.assertEqual(cpu.regs[2], 15)
        self.assertEqual((cpu.acc, cpu.carry), (3, 1))

    def test_io_ports(self):
        cpu = Intel4004()
        cpu.load(assemble("FIM P0, 0x50\nSRC P0\nRDR\nXCH R4\nLDM 7\nWRR\nWMP" + HALT).binary())
        cpu.rom_inputs[5] = 0xC
        cpu.run()
        self.assertEqual(cpu.regs[4], 0xC)
        self.assertEqual(cpu.rom_ports[5], 7)
        self.assertEqual(cpu.ram_ports[0][1], 7)   # 0x50 -> chip 1


class AssemblerTests(unittest.TestCase):
    def test_syntax_variants(self):
        prog = assemble("""
        VAL EQU 0x1F
        TWO = 2
        lbl, FIM 3P, VAL
             FIM R4R5, 1FH
             LDM TWO*3
             JUN $
             DB 'A', "BC", 1010B, $FF
        """)
        self.assertEqual(prog.binary(), bytes([0x26, 0x1F, 0x24, 0x1F, 0xD6, 0x40, 0x05,
                                               0x41, 0x42, 0x43, 0x0A, 0xFF]))

    def test_disassemble_roundtrip(self):
        prog = assemble("ISZ R3, 0x10\nORG 0x10\nFIM P2, 0x34\nSRC P2\nWR3")
        self.assertEqual(disassemble(prog.image, 0), ("ISZ R3,0x010", 2))
        self.assertEqual(disassemble(prog.image, 0x10), ("FIM P2,0x34", 2))
        self.assertEqual(disassemble(prog.image, 0x12), ("SRC P2", 1))
        self.assertEqual(disassemble(prog.image, 0x13), ("WR3", 1))

    def test_errors(self):
        for bad in ["LDM 16", "INC R16", "FOO", "JUN nowhere", "FIM P8, 0", "LD R1, R2"]:
            with self.assertRaises(AsmError, msg=bad):
                assemble(bad)


class Examples(unittest.TestCase):
    def run_example(self, name: str) -> str:
        cpu = Intel4004()
        cpu.load(assemble((EXAMPLES / name).read_text()).binary())
        con = Console(0, stream=io.StringIO())
        cpu.on_rom_write = con.write
        self.assertEqual(cpu.run(), "halted")
        return con.text

    def test_hello(self):
        self.assertEqual(self.run_example("hello.asm"),
                         "HELLO, WORLD! THE 4004 TURNS 55 TODAY.\n")

    def test_bcd_add(self):
        self.assertEqual(self.run_example("bcd_add.asm"), "058598744\n")


class Accessible(unittest.TestCase):
    def test_condition_text(self):
        from i4004.describe import condition_text
        self.assertEqual(condition_text(4), "if the accumulator is zero")
        self.assertEqual(condition_text(12), "if the accumulator is not zero")
        self.assertEqual(condition_text(6), "if the accumulator is zero or the carry is 1")
        self.assertEqual(condition_text(8), "always")
        self.assertEqual(condition_text(0), "never")

    def test_instruction_text(self):
        from i4004.describe import instruction_text
        prog = assemble("LDM 5\nJMS 0x123\nISZ R2, 0")
        self.assertEqual(instruction_text(prog.image, 0),
                         "LDM 5. Load 5 into the accumulator.")
        self.assertEqual(instruction_text(prog.image, 1),
                         "JMS hex 123. Call the subroutine at hex 123.")
        self.assertIn("unless it became 0", instruction_text(prog.image, 3))

    def test_list_values(self):
        from i4004.describe import list_values
        self.assertEqual(list_values([4, 0, 3, 0, 0]), "4, 0, 3, then 2 zeros")
        self.assertEqual(list_values([0] * 16), "all 16 are 0")
        self.assertEqual(list_values([1, 2]), "1, 2")

    def test_narrator_reports_changes(self):
        from i4004.describe import StepNarrator
        cpu = Intel4004()
        cpu.load(assemble("LDM 9\nJZ 0\nFIM P0, 0x21\nSRC P0\nWRM\nNOP").binary())
        lines = []
        narrator = StepNarrator(lines.append)
        for _ in range(6):
            narrator.step(cpu)
        self.assertTrue(lines[0].endswith("Result: accumulator now 9."))
        self.assertIn("did not jump", lines[1])
        self.assertIn("R0 now 2, R1 now 1", lines[2])
        self.assertIn("wrote 9 to RAM bank 0, chip 0, register 2, character 1", lines[4])
        self.assertTrue(lines[5].endswith("No change."))

    def test_cli_accessible_run(self):
        import contextlib
        from i4004.cli import main
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            main(["run", str(EXAMPLES / "bcd_add.asm"), "--console", "--accessible"])
        self.assertEqual(out.getvalue(), "Output: 058598744\n")
        report = err.getvalue()
        self.assertIn("Accumulator: 10. Carry: 0.", report)
        self.assertIn("Characters: 6, 2, 9, 5, 1, 4, 1, 3, then 8 zeros.", report)
        self.assertNotIn("[", report)


if __name__ == "__main__":
    unittest.main()
