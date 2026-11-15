; hello.asm -- print a string on the console device (ROM port 0).
; Run with:  python -m i4004 run examples/hello.asm --console
;
; The 4004 has no byte-wide output, so each character goes out as two
; 4-bit WRR writes: high nibble first, then low nibble.

        ORG  0
start:  FIM  P1, 0x00       ; R2R3 = 00 -> SRC selects ROM chip/port 0
        SRC  P1
        FIM  P0, msg        ; R0R1 = pointer to the message (same page)

loop:   FIN  P2             ; R4R5 = ROM[current page : R0R1]
        LD   R4
        JNZ  emit           ; non-zero high nibble -> print it
        LD   R5
        JZ   done           ; 0x00 terminator -> stop
emit:   LD   R4
        WRR                 ; high nibble
        LD   R5
        WRR                 ; low nibble
        ISZ  R1, loop       ; bump pointer low nibble, loop if no wrap
        INC  R0             ; carry into high nibble
        JUN  loop

done:   JUN  done           ; the 4004 has no HALT; a jump-to-self stops us

msg:    DB   "HELLO, WORLD! THE 4004 TURNS 55 TODAY.", 10, 0
