`ifndef _CASE_COMMON_SV
`define _CASE_COMMON_SV

// Common part of the tests that were added later (case3, case4). The original tests
// case0 / case1 / case2 do not use it, and no original component was changed for it.
//
//   pkt_order_checker   watches the output of the formatter: records from which channel each
//                       packet comes, so that a test can check the order of the packets, and
//                       checks the fmt_grant / fmt_start / fmt_end protocol
//   case_common_vseq    base class of all virtual sequences of case3 and case4: register
//                       access with a check, plain bus transfers, handles
//   case_common_test    base class of case3 and case4: creates the checker, adds a timeout
//                       and an end-of-test check

`include "base_test.sv"

// Watches the formatter output at the falling clock edge, when all DUT outputs are stable,
// so it does not depend on the timing of clocking blocks.
//  - It records the channel id of every packet (fmt_start is high for one clock at the first
//    word). The scoreboard compares each packet with the expected packet of the same channel;
//    it does not look at the order of the packets of different channels, and it does not
//    notice a packet that never comes out. check_order() covers both.
//  - It checks the protocol that nothing else in the testbench checks: a packet starts only
//    while fmt_grant is high, a packet does not start before the previous one has ended, and
//    fmt_end is high exactly on the last word of every packet.
class pkt_order_checker extends uvm_component;
    virtual interface_formatter vif;
    local int pkts[$];              // channel id of every packet so far, in order
    local int n_checked;            // number of packets already checked by check_order()
    local bit in_pkt;               // a packet is being sent
    local int pkt_len;              // its length
    local int word_idx;             // and the position of the current word in it

    `uvm_component_utils(pkt_order_checker)

    function new(string name = "pkt_order_checker", uvm_component parent = null);
        super.new(name, parent);
    endfunction

    virtual task run_phase(uvm_phase phase);
        bit end_expected;
        forever begin
            @(negedge vif.clk);
            if(vif.rstn !== 1'b1)
                continue;
            if(vif.fmt_start === 1'b1) begin
                pkts.push_back(vif.fmt_chid);
                if(in_pkt)
                    `uvm_error("fmt_check", $sformatf("ERROR! a packet of channel %0d starts before the previous packet has ended", vif.fmt_chid))
                if(vif.fmt_grant !== 1'b1)
                    `uvm_error("fmt_check", $sformatf("ERROR! a packet of channel %0d starts without fmt_grant", vif.fmt_chid))
                in_pkt = 1;
                pkt_len = vif.fmt_length;
                word_idx = 0;
            end
            else if(in_pkt) begin
                word_idx++;
            end
            // fmt_end has to be high on the last word of a packet and nowhere else
            end_expected = in_pkt && (word_idx == pkt_len - 1);
            if(vif.fmt_end !== end_expected) begin
                if(in_pkt)
                    `uvm_error("fmt_check", $sformatf("ERROR! fmt_end is %b at word %0d of a %0d-word packet", vif.fmt_end, word_idx, pkt_len))
                else
                    `uvm_error("fmt_check", "ERROR! fmt_end is high while no packet is being sent")
            end
            if(end_expected)
                in_pkt = 0;
        end
    endtask

    // Compare the packets that came out since the last call with 'expected' (channel ids in
    // the expected order). Too many, too few and wrongly ordered packets are all reported.
    function void check_order(string step, int expected[$]);
        int got[$];
        bit same;
        for(int i = n_checked; i < pkts.size(); i++)
            got.push_back(pkts[i]);
        n_checked = pkts.size();
        same = (got.size() == expected.size());
        foreach(got[i])
            if(same && got[i] != expected[i])
                same = 0;
        if(!same)
            `uvm_error("order_check", $sformatf("ERROR! %s: the packets came out of MCDF in the order %s, expected %s", step, ids2str(got), ids2str(expected)))
        else
            `uvm_info("order_check", $sformatf("%s: the packets came out of MCDF in the expected order %s", step, ids2str(got)), UVM_LOW)
    endfunction

    // number of packets that came out after the last check_order()
    function int unchecked();
        return pkts.size() - n_checked;
    endfunction

    // Wait until n packets have come out since the last check_order(), at most max_clocks
    // clocks. It returns at a rising clock edge: driver_bus only makes a correct transfer when
    // it gets the transfer in the time step of a rising edge. A transfer that starts at another
    // time loses its first clock (psel and penable go high together), so the sequence that
    // continues after this task must not start at the falling edge.
    task wait_packets(int n, int max_clocks);
        for(int i = 0; i < max_clocks && unchecked() < n; i++)
            @(negedge vif.clk);
        @(posedge vif.clk);
    endtask

    // "ch2 x2, ch1 x4" for the queue 2 2 1 1 1 1
    function string ids2str(int q[$]);
        string s;
        int n;
        if(q.size() == 0)
            return "(no packet)";
        s = "";
        n = 1;
        for(int i = 1; i <= q.size(); i++) begin
            if(i < q.size() && q[i] == q[i-1]) begin
                n++;
            end else begin
                if(s != "")
                    s = {s, ", "};
                s = {s, $sformatf("ch%0d x%0d", q[i-1], n)};
                n = 1;
            end
        end
        return s;
    endfunction

endclass


// Base class of all virtual sequences of case3 and case4. body() has to call get_handles()
// first: p_sequencer is only known when the sequence runs.
class case_common_vseq extends uvm_sequence;
    uvm_reg ctrl_regs[3];           // control register of channel 0, 1, 2
    uvm_reg stat_regs[3];           // status register of channel 0, 1, 2
    pkt_order_checker pkt_chk;
    virtual interface_bus bus_vif;  // the register bus, for transfers the bus driver cannot make

    `uvm_object_utils(case_common_vseq)
    `uvm_declare_p_sequencer(virtual_sqr)

    function new(string name = "case_common_vseq");
        super.new(name);
    endfunction

    virtual function void get_handles();
        ctrl_regs[0] = p_sequencer.p_rm.chnl0_ctrl_reg;
        ctrl_regs[1] = p_sequencer.p_rm.chnl1_ctrl_reg;
        ctrl_regs[2] = p_sequencer.p_rm.chnl2_ctrl_reg;
        stat_regs[0] = p_sequencer.p_rm.chnl0_stat_reg;
        stat_regs[1] = p_sequencer.p_rm.chnl1_stat_reg;
        stat_regs[2] = p_sequencer.p_rm.chnl2_stat_reg;
        if(!uvm_config_db#(pkt_order_checker)::get(p_sequencer, "", "pkt_chk", pkt_chk))
            `uvm_fatal("case_common_vseq", "no pkt_order_checker found: the test has to be derived from case_common_test")
        if(!uvm_config_db#(virtual interface_bus)::get(p_sequencer, "", "bus_vif", bus_vif))
            `uvm_fatal("case_common_vseq", "no bus interface found: the test has to be derived from case_common_test")
    endfunction

    // read a register with the register model and compare it with the expected value
    virtual task check_reg(uvm_reg rg, uvm_reg_data_t expected, string step);
        uvm_status_e status;
        uvm_reg_data_t value;
        rg.read(status, value);
        if(value != expected)
            `uvm_error("reg_check", $sformatf("ERROR! %s: %s is 0x%0x, expected 0x%0x", step, rg.get_name(), value, expected))
        else
            `uvm_info("reg_check", $sformatf("%s: %s is 0x%0x as expected", step, rg.get_name(), value), UVM_HIGH)
    endtask

    // write transfer directly on the register bus, without the register model (any address)
    virtual task bus_write(bit [7:0] bus_addr, bit [31:0] bus_data);
        sequence_bus reg_seq;
        `uvm_do_on_with(reg_seq, p_sequencer.reg_sqr, {cmd == `WRITE; addr == local::bus_addr; data == local::bus_data; })
    endtask

    // read transfer directly on the register bus. bus_wdata is put on the write data bus
    // during the read; MCDF has to ignore it.
    virtual task bus_read(bit [7:0] bus_addr, bit [31:0] bus_wdata, output bit [31:0] bus_rdata);
        sequence_bus reg_seq;
        `uvm_do_on_with(reg_seq, p_sequencer.reg_sqr, {cmd == `READ; addr == local::bus_addr; data == local::bus_wdata; })
        bus_rdata = reg_seq.data;
    endtask

    // The order in which the arbiter has to serve the channels when all of them have data:
    // the smaller priority value first, the smaller channel number if both are equal.
    function void sort_by_prio(int prio[3], output int order[3]);
        int t;
        order = '{0, 1, 2};
        for(int i = 0; i < 2; i++)
            for(int j = 0; j < 2 - i; j++)
                if(prio[order[j]] > prio[order[j+1]]) begin
                    t = order[j];
                    order[j] = order[j+1];
                    order[j+1] = t;
                end
    endfunction

endclass


// Base class of case3 and case4
class case_common_test extends base_test;
    pkt_order_checker pkt_chk;

    `uvm_component_utils(case_common_test)

    function new(string name = "case_common_test", uvm_component parent = null);
        super.new(name, parent);
    endfunction

    virtual function void build_phase(uvm_phase phase);
        super.build_phase(phase);
        pkt_chk = pkt_order_checker::type_id::create("pkt_chk", this);
        uvm_config_db#(pkt_order_checker)::set(this, "v_sqr", "pkt_chk", pkt_chk);
    endfunction

    virtual function void connect_phase(uvm_phase phase);
        super.connect_phase(phase);
        pkt_chk.vif = env.fmt_agt.mon.vif;
        uvm_config_db#(virtual interface_bus)::set(this, "v_sqr", "bus_vif", env.reg_agt.drv.vif);
    endfunction

    // Timeout. These tests take less than 150 us of simulated time. If a channel never takes
    // its data (its driver then waits for ready for ever), the test ends here with a UVM_FATAL
    // after 1 ms. Without it a hung test runs until a wall-clock limit stops it (WALL_LIMIT in
    // sim_verilator/Makefile: 1800 s, 600 s in the mutation test) or, in a flow without such a
    // limit, until +UVM_TIMEOUT (1 s of simulated time, hours of wall-clock time). The delay is
    // written here, so it is counted in the time unit of the test and not in the one the UVM
    // library was compiled with. The process ends with the run phase.
    virtual task run_phase(uvm_phase phase);
        fork
            begin
                #1ms;
                `uvm_fatal("TIMEOUT", "the test did not end within 1 ms of simulated time; probably a channel waits for ready for ever")
            end
        join_none
    endtask

    // End-of-test check. The order checks of the sequences cover every packet up to the last
    // one they look at; this adds what can only be seen at the end:
    //   - no packet came out after the last order check (an extra packet at the very end would
    //     only block the scoreboard, which waits for an expected packet that never comes)
    //   - the reference model has no expected packet left: every expected packet came out and
    //     was compared (a stuck scoreboard stops taking expected packets)
    virtual function void check_phase(uvm_phase phase);
        super.check_phase(phase);
        if(pkt_chk.unchecked() != 0)
            `uvm_error("end_check", $sformatf("ERROR! %0d packet(s) came out of MCDF after the last order check", pkt_chk.unchecked()))
        for(int i = 0; i < 3; i++)
            if(!env.mdl.out_tlm_fifos[i].is_empty())
                `uvm_error("end_check", $sformatf("ERROR! channel %0d: the reference model still has expected packet(s); they did not come out of MCDF or were never compared", i))
    endfunction

endclass

`endif
