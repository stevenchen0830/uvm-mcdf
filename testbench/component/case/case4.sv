`ifndef _CASE4_SV
`define _CASE4_SV

`include "case_common.sv"

// case4: register test
//
// configure phase (case4_cfg_vseq), no data is sent:
//   1. while reset is active, pwrite is high on the bus (psel is low, so the bus protocol
//      allows any value): nothing may be written. After reset the control registers read 0x07
//      and the status registers 0x20 (empty FIFOs; a status register is loaded from its FIFO
//      in every clock, so its own reset value can never be seen on the bus)
//   2. every control register is written with all 64 legal values and read back. The three
//      registers always get different values and are written in the order 0, 1, 2 for even
//      values of the loop and 2, 1, 0 for odd ones, so a write that also changes another
//      control register is found in one of the two orders.
//   3. the reserved bits [31:6] of the control registers cannot be written, they read 0
//   4. the status registers are read-only: every write to them is followed directly by a read
//      of the same register (a status register is reloaded in every clock, so a later read
//      could not see a value that was written into it)
//   5. an access to an address without a register does not change any register
//   6. a read transfer does not change a register, whatever is on the write data bus
//   7. a write transfer for another slave on the same bus (psel low, penable and pwrite high)
//      does not change any register
// main phase (case4_vseq):
//   8. the status registers show the free space of the channel FIFOs while data is waiting in
//      them, also during back-to-back bus transfers and while the channel is disabled. Each of
//      the bits [5:0] of every status register is checked once as 1 and once as 0 (bits [31:6]
//      are always 0). 14 packets go through MCDF; the scoreboard compares them and the order
//      in which they come out is checked, also when a channel still has data at the end of its
//      packet while channels with a better priority are waiting.

// -- virtual sequence for register
class case4_cfg_vseq extends case_common_vseq;
    sequence_bus reg_seq;

    `uvm_object_utils(case4_cfg_vseq)

    function new(string name = "case4_cfg_vseq");
        super.new(name);
    endfunction

    // check all six registers: the control registers against the given values, the status
    // registers must show empty FIFOs (0x20)
    virtual task check_all_regs(uvm_reg_data_t exp0, uvm_reg_data_t exp1, uvm_reg_data_t exp2, string step);
        check_reg(ctrl_regs[0], exp0, step);
        check_reg(ctrl_regs[1], exp1, step);
        check_reg(ctrl_regs[2], exp2, step);
        foreach(stat_regs[i])
            check_reg(stat_regs[i], 32'h20, step);
    endtask

    // write a status register and read it back directly: it must still show an empty FIFO
    virtual task write_stat_reg(int chnl, bit [31:0] value);
        bit [31:0] rdata;
        bit [7:0] a;
        a = 8'h10 + 4*chnl;
        bus_write(a, value);
        bus_read(a, 32'h0, rdata);
        if(rdata != 32'h20)
            `uvm_error("reg_check", $sformatf("ERROR! status register at address 0x%0x returns 0x%0x directly after a write of 0x%0x, expected 0x20 (read-only)", a, rdata, value))
    endtask

    // A write transfer on the bus that is meant for another slave: psel stays low, penable and
    // pwrite go high as in a write of MCDF. MCDF has to ignore it. The bus driver cannot make
    // such a transfer, so the signals are driven here, while the driver is idle.
    virtual task other_slave_write(bit [7:0] a, bit [31:0] d);
        @(posedge bus_vif.clk);
        bus_vif.drv_ck.psel    <= 1'b0;
        bus_vif.drv_ck.penable <= 1'b0;
        bus_vif.drv_ck.pwrite  <= 1'b1;
        bus_vif.drv_ck.paddr   <= a;
        bus_vif.drv_ck.pwdata  <= d;
        @(posedge bus_vif.clk);
        bus_vif.drv_ck.penable <= 1'b1;
        @(posedge bus_vif.clk);
        bus_vif.drv_ck.penable <= 1'b0;
        bus_vif.drv_ck.pwrite  <= 1'b0;
        bus_vif.drv_ck.pwdata  <= 0;
        @(posedge bus_vif.clk);
    endtask

    virtual task body();
        uvm_status_e status;
        bit [31:0] rdata;
        int i;
        // write data with reserved bits set
        bit [31:0] patterns[4] = '{32'hFFFF_FFFF, 32'hFFFF_FFC0, 32'hAAAA_AAAA, 32'h5555_5555};
        // the control registers keep these values during step 4 to 7
        uvm_reg_data_t keep[3] = '{32'h15, 32'h2A, 32'h33};

        if(starting_phase != null)
            starting_phase.raise_objection(this);
        get_handles();

        // 1. pwrite is high while reset is active, until the first clock after reset. The bus
        //    driver sets all bus signals to 0 at time 0 (and on a 4-state simulator again at
        //    the falling edge of rstn at 10 ns), so this starts at 20 ns. psel stays 0.
        fork
            begin
                #20;
                bus_vif.pwrite = 1'b1;
                @(posedge bus_vif.clk iff bus_vif.rstn === 1'b1);
                bus_vif.pwrite <= 1'b0;
            end
        join_none

        // bring the register model to the reset state as well
        p_sequencer.p_rm.reset();

        // 1. reset values: nothing has been written since the reset
        `uvm_info("case4", "step 1: values after reset (pwrite was high during reset)", UVM_LOW)
        foreach(ctrl_regs[k])
            check_reg(ctrl_regs[k], 32'h07, "reset value");
        foreach(stat_regs[k])
            check_reg(stat_regs[k], 32'h20, "after reset, empty FIFO");

        // 2. all 64 legal values: write the three registers, then read them back. They always
        //    get three different values, so a write that goes to the wrong register is found.
        //    Even v: written in the order 0, 1, 2; odd v: in the order 2, 1, 0. A write that
        //    changes another register as well is hidden when that register is written after
        //    it, but found in the other order.
        `uvm_info("case4", "step 2: write and read back all values of the control registers", UVM_LOW)
        for(int v = 0; v < 64; v++) begin
            for(int k = 0; k < 3; k++) begin
                i = (v % 2 == 0) ? k : 2 - k;
                ctrl_regs[i].write(status, (v + 21*i) % 64);
            end
            for(int k = 0; k < 3; k++)
                check_reg(ctrl_regs[k], (v + 21*k) % 64, "write and read back");
        end

        // 3. reserved bits [31:6] are written with ones: they still read 0 and the six real
        //    bits take the written value (again in both orders)
        `uvm_info("case4", "step 3: reserved bits of the control registers", UVM_LOW)
        for(int p = 0; p < 4; p++) begin
            for(int k = 0; k < 3; k++) begin
                i = (p % 2 == 0) ? k : 2 - k;
                ctrl_regs[i].write(status, patterns[(p + i) % 4]);
            end
            for(int k = 0; k < 3; k++)
                check_reg(ctrl_regs[k], patterns[(p + k) % 4] & 32'h3F, "reserved bits");
        end

        // 4. the status registers are read-only: writing to them changes nothing
        `uvm_info("case4", "step 4: write to the read-only status registers", UVM_LOW)
        foreach(ctrl_regs[k])
            ctrl_regs[k].write(status, keep[k]);
        for(int k = 0; k < 3; k++) begin
            write_stat_reg(k, 32'hFFFF_FFFF);
            write_stat_reg(k, 32'h0000_0000);
        end
        check_all_regs(keep[0], keep[1], keep[2], "after writing to the status registers");

        // 5. all addresses without a register: a write must not change any register. They are
        //    read as well, the read data is not defined and not checked.
        `uvm_info("case4", "step 5: addresses without a register", UVM_LOW)
        for(int a = 0; a < 256; a++) begin
            if(a inside {8'h00, 8'h04, 8'h08, 8'h10, 8'h14, 8'h18})
                continue;
            bus_write(a, 32'hFFFF_FFFF);
            bus_read(a, 32'h0000_0000, rdata);
        end
        check_all_regs(keep[0], keep[1], keep[2], "after accesses to addresses without a register");

        // 6. a read transfer never writes: all ones are on the write data bus during the read
        `uvm_info("case4", "step 6: read transfers with data on the write data bus", UVM_LOW)
        for(int k = 0; k < 3; k++) begin
            bus_read(4*k, 32'hFFFF_FFFF, rdata);
            if(rdata != keep[k])
                `uvm_error("reg_check", $sformatf("ERROR! read with data on the write data bus: address 0x%0x returns 0x%0x, expected 0x%0x", 4*k, rdata, keep[k]))
            bus_read(8'h10 + 4*k, 32'hFFFF_FFFF, rdata);
            if(rdata != 32'h20)
                `uvm_error("reg_check", $sformatf("ERROR! read with data on the write data bus: address 0x%0x returns 0x%0x, expected 0x20", 8'h10 + 4*k, rdata))
        end
        check_all_regs(keep[0], keep[1], keep[2], "after reads with data on the write data bus");

        // 7. a write for another slave: first a read of control register k, so that k is the
        //    register MCDF has addressed last, then the foreign write with all ones
        `uvm_info("case4", "step 7: write transfers for another slave on the same bus", UVM_LOW)
        for(int k = 0; k < 3; k++) begin
            check_reg(ctrl_regs[k], keep[k], "before a write for another slave");
            other_slave_write(4*k, 32'hFFFF_FFFF);
        end
        check_all_regs(keep[0], keep[1], keep[2], "after write transfers for another slave");

        // configuration for the main phase, all channels enabled:
        // packet length 8 / 16 / 32 words, priority 2 / 1 / 0
        ctrl_regs[0].write(status, (1<<3) + (2<<1) + 1);
        ctrl_regs[1].write(status, (2<<3) + (1<<1) + 1);
        ctrl_regs[2].write(status, (3<<3) + (0<<1) + 1);
        check_all_regs((1<<3) + (2<<1) + 1, (2<<3) + (1<<1) + 1, (3<<3) + (0<<1) + 1, "configuration for the main phase");

        // send IDLE command
        `uvm_do_on_with(reg_seq, p_sequencer.reg_sqr, {cmd==`IDLE; } )

        #500;
        if(starting_phase != null)
            starting_phase.drop_objection(this);
    endtask

endclass

// -- virtual sequence for data channel and formatter
class case4_vseq extends case_common_vseq;
    sequence_formatter fmt_seq;

    // packet length code, packet length and priority of channel 0 / 1 / 2, written by
    // case4_cfg_vseq
    int len_code[3] = '{1, 2, 3};
    int pkt_len[3]  = '{8, 16, 32};
    int prio[3]     = '{2, 1, 0};

    `uvm_object_utils(case4_vseq)

    function new(string name = "case4_vseq");
        super.new(name);
    endfunction

    // control register value of a channel in the main phase, with the given enable bit
    function uvm_reg_data_t ctrl_value(int chnl, bit en);
        return (len_code[chnl] << 3) + (prio[chnl] << 1) + en;
    endfunction

    // send 'words' data words to a channel as one transaction. The task returns when MCDF has
    // taken all of them. pkt is the packet number in the data words: every transaction gets
    // different data.
    virtual task send_data(int chnl, int words, int pkt);
        sequence_channel chnl_seq;
        `uvm_do_on_with(chnl_seq, p_sequencer.chnl_sqrs[chnl], {ntrans == 1; ch_id == local::chnl; pkt_id == local::pkt; data_nidles == 0; pkt_nidles == 1; data_size == local::words; })
    endtask

    // Send data to a channel while the register bus is busy all the time: back-to-back reads
    // of control register 0 run until all the data has been taken, then two more (a status
    // register follows its FIFO one clock later). psel does not go low in between.
    virtual task send_data_bus_busy(int chnl, int words, int pkt);
        bit done;
        bit [31:0] rdata;
        fork
            begin
                send_data(chnl, words, pkt);
                done = 1;
            end
            begin
                while(!done)
                    bus_read(8'h00, 32'h0, rdata);
            end
        join
        bus_read(8'h00, 32'h0, rdata);
        bus_read(8'h00, 32'h0, rdata);
    endtask

    // compare the three status registers with the free space of the three FIFOs
    virtual task check_stat(int free[3], string step);
        foreach(stat_regs[i])
            check_reg(stat_regs[i], free[i], step);
    endtask

    virtual task body();
        if(starting_phase != null)
            starting_phase.raise_objection(this);
        get_handles();
        #100;

        // set the sequence send to formatter, fmt_seq doesn't consume time
        `uvm_do_on_with(fmt_seq, p_sequencer.fmt_sqr, {fifo==ULTRA_FIFO; bandwidth == ULTRA_WIDTH; } );

        // 8. status registers while data is waiting in the channel FIFOs. Free space shown:
        //
        //    round  holder   channel a                 channel b                 status 0 / 1 / 2
        //    1      ch2      ch0:  9, then 16 words    ch1: 22, then 32 words    0x17/0x0A/0x20, 0x10/0x00/0x20
        //    2      ch0      ch1: 11, then 16 words    ch2: 22, then 32 words    0x20/0x15/0x0A, 0x20/0x10/0x00
        //    3      ch1      ch2: 11, then 32 words    ch0: 24 words             0x08/0x20/0x15, 0x08/0x20/0x00
        //
        //    Status register 0 shows 0x17 and 0x08, the others 0x15 and 0x0A: these pairs are
        //    complementary in bits [4:0], and bit 5 is 1 only in 0x20 (empty FIFO), which every
        //    status register shows as well. So each of the bits [5:0] is checked once as 1 and
        //    once as 0. In each check of the table the three values are different, so a status
        //    register that shows the FIFO of another channel is found.
        `uvm_info("case4", "step 8: status registers while data is waiting in the channel FIFOs", UVM_LOW)
        //         round  holder  a  a_words  b  b_words  a_total  b_total  holder_extra
        park_round(1,     2,      0, 9,       1, 22,      16,      32,      0);
        park_round(2,     0,      1, 11,      2, 22,      16,      32,      1);
        park_round(3,     1,      2, 11,      0, 24,      32,      24,      0);
        #100;   // the monitor of the register bus reports a transfer one clock after it ends

        if(starting_phase != null)
            starting_phase.drop_objection(this);
    endtask

    // One round of step 8:
    //  - The holder gets the first 4 words of a packet. MCDF takes them out of the channel FIFO
    //    at once and then waits for the rest of this packet; meanwhile it serves no other
    //    channel. So the data sent to channel a and b stays in their FIFOs and the status
    //    registers can be compared exactly.
    //  - a gets a_words words while the bus is busy without a break, and its status register
    //    is read in the same burst. Then b gets b_words words, then both are filled up to
    //    a_total and b_total words (whole packets). The status registers are checked each time.
    //  - a and b are disabled: their data stays in the FIFOs and the status registers still
    //    show it. A disabled channel takes no new data, but the data already in its FIFO is sent.
    //  - The rest of the holder's packet is sent (plus holder_extra whole packets), and all the
    //    data goes out: first the holder's packet, then all the channels that have data in the
    //    order of their priority, each with all its packets (no new data arrives meanwhile).
    //    In round 2 the holder (priority 2, the worst) still has a packet at the end of its
    //    first one and has to wait for channel 2 and 1.
    //  - The status registers must show empty FIFOs, then a and b are enabled again.
    virtual task park_round(int round, int holder, int a, int a_words, int b, int b_words, int a_total, int b_total, int holder_extra);
        uvm_status_e status;
        int pkt;
        int free[3];
        int npk[3];
        int order[3];
        int expected[$];
        string step;

        pkt = 16 * round;
        step = $sformatf("round %0d", round);

        send_data(holder, 4, pkt);
        send_data_bus_busy(a, a_words, pkt + 1);
        check_reg(stat_regs[a], 32 - a_words, {step, ", read during back-to-back bus transfers"});

        send_data(b, b_words, pkt + 2);
        #100;   // a status register follows its FIFO one clock later
        free[holder] = 32;
        free[a] = 32 - a_words;
        free[b] = 32 - b_words;
        check_stat(free, {step, ", data is waiting in two FIFOs"});

        if(a_total > a_words)
            send_data(a, a_total - a_words, pkt + 3);
        if(b_total > b_words)
            send_data(b, b_total - b_words, pkt + 4);
        #100;
        free[a] = 32 - a_total;
        free[b] = 32 - b_total;
        check_stat(free, {step, ", more data is waiting"});

        ctrl_regs[a].write(status, ctrl_value(a, 0));
        ctrl_regs[b].write(status, ctrl_value(b, 0));
        #100;
        check_stat(free, {step, ", the two channels with data are disabled"});

        send_data(holder, pkt_len[holder] - 4 + holder_extra * pkt_len[holder], pkt + 5);
        #3us;   // wait untill all the data has been transferred through MCDF
        free = '{32, 32, 32};
        check_stat(free, {step, ", all data has gone out, two channels still disabled"});
        ctrl_regs[a].write(status, ctrl_value(a, 1));
        ctrl_regs[b].write(status, ctrl_value(b, 1));

        npk[a] = a_total / pkt_len[a];
        npk[b] = b_total / pkt_len[b];
        npk[holder] = holder_extra;
        sort_by_prio(prio, order);
        expected.push_back(holder);
        foreach(order[k])
            repeat(npk[order[k]]) expected.push_back(order[k]);
        pkt_chk.check_order(step, expected);
    endtask

endclass


class case4 extends case_common_test;
    `uvm_component_utils(case4)

    function new(string name = "case4", uvm_component parent = null);
        super.new(name, parent);
    endfunction

    virtual function void build_phase(uvm_phase phase);
        super.build_phase(phase);

        uvm_config_db #(uvm_object_wrapper)::set(this, "v_sqr.configure_phase", "default_sequence", case4_cfg_vseq::type_id::get());
        uvm_config_db #(uvm_object_wrapper)::set(this, "v_sqr.main_phase", "default_sequence", case4_vseq::type_id::get());
    endfunction

endclass


`endif
