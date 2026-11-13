"""
Cross-check of the oracle against the only VCS artefact shipped with the repo: sim/signal.rc
(Verdi nWave session saved by the author at upstream commit 9059941, 2022-02-16; viewport
"zoom 0 27305.35", cursor 126, marker 825).  At that commit the register bus was the old
cmd/cmd_addr interface (1 clock per write, posedge + 2 negedges per read, 1 clock for IDLE) and
case0 used pkt_nidles==3 for channel 2 with the formatter sequence forked in parallel.
The data path (channel driver, formatter driver, slave_fifo, arbiter, formater) is byte-identical
to today's code, so the same model must reproduce that run.
"""
import sys
import os; sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import mcdf_oracle as m

def run(cfg_done, params, final_delay=5000, label=''):
    sim = m.Sim()
    st = {}
    # registers: values as written by the cfg sequence (bus protocol itself is not modelled here)
    def main_seq():
        yield ('delay', cfg_done + 500)          # cfg vseq: IDLE item done at cfg_done, then #500
        st['main_start'] = sim.t
        yield ('delay', 100)
        m.fmt_config(sim, 2048, 1)
        box = {}
        for ch in range(3):
            nt, ds, dn, pn = params[ch]
            sim.spawn(m.chan_seq(sim, ch, nt, ds, dn, pn, lambda t, ch=ch: box.__setitem__(ch, t)))
            while ch not in box:
                yield ('posedge',)
            if ch < 2:
                yield ('delay', 500 - (sim.t - box[ch]))
        st['ch_done'] = dict(box)
        yield ('delay', final_delay - (sim.t - box[2]))
        st['end'] = sim.t
        sim.finished = True
    gc = m.fmt_consume(sim); sim.prio[id(gc)] = 0; sim.spawn(gc)
    sim.spawn(m.fmt_receive(sim))
    sim.spawn(main_seq())
    while not sim.finished:
        sim.step()
        if sim.k == 11:   # registers take their configured values well before traffic
            sim.dut.mem[0], sim.dut.mem[1], sim.dut.mem[2] = 0x01, 0x09, 0x11
    first_valid = min(k for k in sim.hist if sim.hist[k]['valid'][0])
    print('%-34s main_start=%d first ch0 drive posedge=%d ns ch_done=%s end=%d ns  -> 1.01*end=%.2f  pkts=%d' % (
        label, st['main_start'], m.tpos(first_valid), st['ch_done'], st['end'], 1.01 * st['end'], len(sim.out_pkts)))
    return st

print('signal.rc (VCS, author):  cursor=126  marker=825  zoom end=27305.35 (= 1.01 * 27035.00)')
# upstream 9059941: old bus driver -> IDLE item done at 215 ns; ch2 pkt_nidles == 3
run(215, [(40, 8, 0, 1), (20, 16, 0, 2), (10, 32, 0, 3)], label='model @9059941 (rc file commit)')
# upstream 98acdb2 (pre-APB2, final case0): ch2 pkt_nidles == 4
run(215, [(40, 8, 0, 1), (20, 16, 0, 2), (10, 32, 0, 4)], label='model @98acdb2 (old bus, case0 now)')
# today's code: APB-like bus -> IDLE item done at 235 ns
run(235, [(40, 8, 0, 1), (20, 16, 0, 2), (10, 32, 0, 4)], label='model @HEAD (APB bus)')
