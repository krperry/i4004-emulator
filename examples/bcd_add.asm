; bcd_add.asm -- 8-digit decimal addition, calculator style.
; Run with:  python -m i4004 run examples/bcd_add.asm --console
;
; The 4004 was designed for the Busicom 141-PF calculator, so it keeps
; numbers as one BCD digit per 4002 RAM character and adds them with
; ADM + DAA.  This adds 27182818 + 31415926 and prints 058598744.
;
; RAM layout (bank 0, chip 0): register 0 = A, register 1 = B,
; least-significant digit in character 0.

        ORG  0
        LDM  0
        DCL                 ; select RAM bank 0

        FIM  P0, numA       ; ROM table pointer
        FIM  P1, 0x00       ; RAM chip 0, register 0, char 0
        JMS  load8
        FIM  P0, numB
        FIM  P1, 0x10       ; RAM chip 0, register 1, char 0
        JMS  load8

; ---- A = A + B, one decimal digit at a time
        CLC
        FIM  P1, 0x00       ; destination: register 0
        FIM  P2, 0x10       ; source:      register 1
        LDM  8              ; ISZ counts up to 16: 8 iterations
        XCH  R6
addlp:  SRC  P2
        RDM                 ; ACC = B digit
        SRC  P1
        ADM                 ; ACC = B + A + carry
        DAA                 ; decimal adjust, sets carry on overflow
        WRM                 ; store into A
        INC  R3
        INC  R5
        ISZ  R6, addlp
        SRC  P1             ; char 8 of register 0 = final carry
        TCC
        WRM

; ---- print the 9 digits, most significant first
        FIM  P4, 0x00       ; R8R9 -> ROM port 0 (console)
        LDM  7              ; 9 iterations
        XCH  R7
        LDM  8
        XCH  R3             ; start at char 8
prlp:   SRC  P1
        RDM
        XCH  R10            ; save the digit
        SRC  P4
        LDM  3              ; ASCII '0'..'9' = 0x30..0x39
        WRR
        LD   R10
        WRR
        LD   R3
        DAC
        XCH  R3
        ISZ  R7, prlp
        LDM  0              ; newline
        WRR
        LDM  10
        WRR
done:   JUN  done

; ---- copy 8 digits from ROM[R0R1] into RAM starting at SRC address R2R3
load8:  LDM  8
        XCH  R6
ldlp:   FIN  P5             ; R10R11 = ROM byte; digit in R11
        SRC  P1
        LD   R11
        WRM
        ISZ  R1, nowrap     ; 8-bit pointer increment: bump low nibble...
        INC  R0             ; ...and carry into the high nibble on wrap
nowrap: INC  R3
        ISZ  R6, ldlp
        BBL  0

numA:   DB   8,1,8,2,8,1,7,2    ; 27182818, least-significant first
numB:   DB   6,2,9,5,1,4,1,3    ; 31415926
