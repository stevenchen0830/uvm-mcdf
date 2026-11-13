`ifndef _CASE3_SV
`define _CASE3_SV

`include "case_common.sv"

// case3: channel enable / disable and priority test
//
// Rounds 0..2: the three channels are switched off one after the other. In every round:
//   1. one channel is disabled (enable bit = 0) and data is sent to it: its driver raises
//      valid and waits for ready. MCDF must not take any of this data. The scoreboard
//      (do_channel_disable_check) reports every clock in which valid and ready are both high
//      on a disabled channel.
//   2. meanwhile the two other channels send data, in three parts: in the first part the
//      channel with the higher number has the better priority, in the second part the other
//      one, in the third part both have the same priority (then the smaller channel number
//      wins). The order in which the packets come out shows whether the arbiter really uses
//      the priorities that were written to the registers.
//   3. the disabled channel has taken nothing: its driver is still waiting and its FIFO is
//      empty (status register 0x20)
//   4. the channel is enabled again and the data that was waiting has to come out
// Round 3: all three channels send at the same time, with five different sets of priorities,
//   ties included. The packets have to come out in the order of the priorities.
// Round 4: the same once more with a slow downstream: the formatter driver often gives
//   fmt_grant late. The order must not change, and no packet may start before its grant
//   (checked by pkt_order_checker). Only the timing of this round depends on the seed.
//
// Packet length of channel 0 / 1 / 2: 4 / 8 / 16 words. Every channel sends 448 words in
// total, so the scoreboard has to compare 112 + 56 + 28 = 196 packets.

// -- virtual sequence for data channel and formatter
class case3_vseq extends case_common_vseq;
    sequence_channel chnl_seq_off;      // data for the channel that is switched off
    sequence_channel chnl_seq_a;        // data for the two channels that stay enabled
    sequence_channel chnl_seq_b;
    sequence_channel chnl_seqs[3];      // round 3 and 4: one sequence per channel
    sequence_formatter fmt_seq;
    bit off_done;                       // set when MCDF has taken all the data of chnl_seq_off

    // Rounds 0..2, priority of channel 0 / 1 / 2 (0 is the highest priority): three rows for
    // every round, one for each part. Of the two channels that stay enabled, the one with the
    // higher number has the better priority in the first part, the worse one in the second part
    // and the same one in the third part. So for every pair of channels the arbiter has to
    // decide in both directions and in a tie. Every channel uses all four priority values.
    int prio_tab[9][3] = '{'{3, 2, 0}, '{3, 1, 3}, '{3, 2, 2},      // round 0: channel 0 is disabled
                           '{2, 3, 1}, '{0, 3, 2}, '{1, 3, 1},      // round 1: channel 1 is disabled
                           '{1, 0, 2}, '{2, 3, 2}, '{1, 1, 3}};     // round 2: channel 2 is disabled

    // Round 3, all three channels send. Winner and second place with ties, and every branch of
    // the three-request case in arbiter.v: channel 0 best (1 1 2, 1 2 1), channel 1 best with
    // channel 2 not better than channel 0 (1 0 2) or better than it (2 1 1), channel 2 best (2 2 0).
    int prio3_tab[5][3] = '{'{2, 1, 1}, '{1, 1, 2}, '{1, 2, 1}, '{1, 0, 2}, '{2, 2, 0}};

    `uvm_object_utils(case3_vseq)

    function new(string name = "case3_vseq");
        super.new(name);
    endfunction

    // value of a control register: the packet length code is the channel number (4/8/16 words)
    function uvm_reg_data_t ctrl_value(int id, int prio, bit en);
        return (id << 3) + (prio << 1) + en;
    endfunction

    // number of packets of channel id when it sends 'words' words (packet length 4 << id)
    function int npkts(int id, int words);
        return words / (4 << id);
    endfunction

    // the channel the arbiter has to choose from a and b with the priorities of row 'row' of
    // prio_tab: the smaller priority value wins, the smaller channel number if both are equal
    function int winner(int a, int b, int row);
        if(prio_tab[row][a] != prio_tab[row][b])
            return (prio_tab[row][a] < prio_tab[row][b]) ? a : b;
        return (a < b) ? a : b;
    endfunction

    virtual task body();
        uvm_status_e status;
        if(starting_phase != null)
            starting_phase.raise_objection(this);
        get_handles();
        #100;

        // set the sequence send to formatter, fmt_seq doesn't consume time
        `uvm_do_on_with(fmt_seq, p_sequencer.fmt_sqr, {fifo==ULTRA_FIFO; bandwidth == ULTRA_WIDTH; } );

        for(int id = 0; id < 3; id++)
            one_round(id);

        for(int p = 0; p < 5; p++)
            all_channels_part($sformatf("round 3, part %0d", p), prio3_tab[p], 2, 48 + 2*p);

        // round 4: slow downstream (small FIFO, slow consumer in the formatter driver)
        `uvm_do_on_with(fmt_seq, p_sequencer.fmt_sqr, {fifo==SHORT_FIFO; bandwidth == LOW_WIDTH; } );
        all_channels_part("round 4, slow downstream", '{2, 0, 1}, 4, 64);

        // all data has been transferred through MCDF: every FIFO must be empty again
        foreach(stat_regs[i])
            check_reg(stat_regs[i], 32'h20, "end of the test, the FIFO must be empty");
        #100;   // the monitor of the register bus reports a transfer one clock after it ends

        if(starting_phase != null)
            starting_phase.drop_objection(this);
    endtask

    // one round: channel id is disabled while the two other channels send data
    virtual task one_round(int id);
        uvm_status_e status;
        int a = (id + 1) % 3;           // the two channels that stay enabled
        int b = (id + 2) % 3;
        int row;                        // row of prio_tab
        int win, lose;
        int off_pkt = 16 * id;          // packet numbers in the data words: the data is
        int ab_pkt;                     // different in every round and every part
        int expected[$];
        string step;

        off_done = 0;
        for(int part = 0; part < 3; part++) begin
            row = 3*id + part;
            ab_pkt = 16*id + 4*part;
            step = $sformatf("round %0d, part %0d", id, part);
            `uvm_info("case3", $sformatf("%s: channel %0d is disabled, priority of channel 0/1/2 is %0d/%0d/%0d",
                                         step, id, prio_tab[row][0], prio_tab[row][1], prio_tab[row][2]), UVM_LOW)

            // the priorities of this part; channel id is switched off
            for(int ch = 0; ch < 3; ch++)
                ctrl_regs[ch].write(status, ctrl_value(ch, prio_tab[row][ch], ch != id));

            // 1. data for the disabled channel: valid goes high and the driver waits for
            //    ready until the channel is enabled again at the end of the round
            if(part == 0) begin
                fork
                    begin
                        `uvm_do_on_with(chnl_seq_off, p_sequencer.chnl_sqrs[id], {ntrans == 2; ch_id==local::id; pkt_id==local::off_pkt; data_nidles==0; pkt_nidles==1; data_size==16; });
                        off_done = 1;
                    end
                join_none
            end

            // 2. the two other channels work normally meanwhile, 32 words each
            fork
                begin
                    `uvm_do_on_with(chnl_seq_a, p_sequencer.chnl_sqrs[a], {ntrans == 2; ch_id==local::a; pkt_id==local::ab_pkt; data_nidles==0; pkt_nidles==1; data_size==16; });
                end
                begin
                    `uvm_do_on_with(chnl_seq_b, p_sequencer.chnl_sqrs[b], {ntrans == 2; ch_id==local::b; pkt_id==local::ab_pkt; data_nidles==1; pkt_nidles==2; data_size==16; });
                end
            join
            #3us;   // wait untill the data of the two enabled channels has been transferred

            //    Both channels start sending in the same clock. The one with the better
            //    priority gets the formatter first and keeps it until it has sent all its
            //    packets, because it always has new data when one of its packets ends. Then
            //    the other channel sends all its packets.
            win = winner(a, b, row);
            lose = (win == a) ? b : a;
            expected.delete();
            repeat(npkts(win, 32)) expected.push_back(win);
            repeat(npkts(lose, 32)) expected.push_back(lose);
            pkt_chk.check_order(step, expected);
        end

        // 3. the disabled channel has taken nothing
        if(off_done)
            `uvm_error("chnl_check", $sformatf("ERROR! channel %0d is disabled but it has taken all the data that was sent to it", id))
        check_reg(stat_regs[id], 32'h20, $sformatf("round %0d, channel %0d is disabled, its FIFO must be empty", id, id));

        // 4. enable the channel again: the data that was waiting has to go through now
        `uvm_info("case3", $sformatf("round %0d: channel %0d is enabled again", id, id), UVM_LOW)
        ctrl_regs[id].write(status, ctrl_value(id, prio_tab[row][id], 1));
        wait(off_done);
        #3us;
        expected.delete();
        repeat(npkts(id, 32)) expected.push_back(id);
        pkt_chk.check_order($sformatf("round %0d, channel %0d enabled again", id, id), expected);
    endtask

    // All three channels send n_trans transactions of 16 words each, starting in the same clock.
    // All data arrives faster than MCDF sends it, so the channel with the best priority keeps
    // the formatter until it has sent all its packets, then the second one, then the third.
    virtual task all_channels_part(string step, int prio[3], int n_trans, int first_pkt);
        uvm_status_e status;
        int order[3];
        int expected[$];

        `uvm_info("case3", $sformatf("%s: all channels enabled, priority of channel 0/1/2 is %0d/%0d/%0d",
                                     step, prio[0], prio[1], prio[2]), UVM_LOW)
        for(int ch = 0; ch < 3; ch++)
            ctrl_regs[ch].write(status, ctrl_value(ch, prio[ch], 1));

        fork
            `uvm_do_on_with(chnl_seqs[0], p_sequencer.chnl_sqrs[0], {ntrans == local::n_trans; ch_id == 0; pkt_id == local::first_pkt; data_nidles == 0; pkt_nidles == 1; data_size == 16; })
            `uvm_do_on_with(chnl_seqs[1], p_sequencer.chnl_sqrs[1], {ntrans == local::n_trans; ch_id == 1; pkt_id == local::first_pkt; data_nidles == 0; pkt_nidles == 1; data_size == 16; })
            `uvm_do_on_with(chnl_seqs[2], p_sequencer.chnl_sqrs[2], {ntrans == local::n_trans; ch_id == 2; pkt_id == local::first_pkt; data_nidles == 0; pkt_nidles == 1; data_size == 16; })
        join

        sort_by_prio(prio, order);
        foreach(order[k])
            repeat(npkts(order[k], 16 * n_trans)) expected.push_back(order[k]);
        // wait until all packets are out (with a slow downstream this takes several us)
        pkt_chk.wait_packets(expected.size(), 5000);
        #200;
        pkt_chk.check_order(step, expected);
    endtask

endclass

// -- virtual sequence for register
class case3_cfg_vseq extends case_common_vseq;
    sequence_bus reg_seq;

    `uvm_object_utils(case3_cfg_vseq)

    function new(string name = "case3_cfg_vseq");
        super.new(name);
    endfunction
    virtual task body();
        uvm_status_e status;
        if(starting_phase != null)
            starting_phase.raise_objection(this);
        get_handles();

        // packet length 4 / 8 / 16, priority 0, all channels enabled
        // (priorities and enable bits are written again in every part of case3_vseq)
        ctrl_regs[0].write(status, {26'h00, 3'd0, 2'b00, 1'b1});
        ctrl_regs[1].write(status, {26'h00, 3'd1, 2'b00, 1'b1});
        ctrl_regs[2].write(status, {26'h00, 3'd2, 2'b00, 1'b1});

        // send IDLE command
        `uvm_do_on_with(reg_seq, p_sequencer.reg_sqr, {cmd==`IDLE; } )

        #500;
        if(starting_phase != null)
            starting_phase.drop_objection(this);
    endtask

endclass


class case3 extends case_common_test;
    `uvm_component_utils(case3)

    function new(string name = "case3", uvm_component parent = null);
        super.new(name, parent);
    endfunction

    virtual function void build_phase(uvm_phase phase);
        super.build_phase(phase);

        uvm_config_db #(uvm_object_wrapper)::set(this, "v_sqr.configure_phase", "default_sequence", case3_cfg_vseq::type_id::get());
        uvm_config_db #(uvm_object_wrapper)::set(this, "v_sqr.main_phase", "default_sequence", case3_vseq::type_id::get());
    endfunction

endclass


`endif
