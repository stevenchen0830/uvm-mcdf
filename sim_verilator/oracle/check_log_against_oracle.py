#!/usr/bin/env python3
"""
Check a uvm-mcdf simulation log (VCS or Verilator port) against the independent oracle
(mcdf_oracle.py, same directory).  The script only READS the log.

    python check_log_against_oracle.py case0 path/to/sim_case0.log
    python check_log_against_oracle.py case1 path/to/sim_case1.log
    python check_log_against_oracle.py case2 path/to/sim_case2.log   (parameters are taken from the log)

What is checked
  * "TestCase Passed: <test>", UVM_ERROR == 0, UVM_FATAL == 0
  * register-bus activity seen by monitor_bus: order, addresses, data and time stamps
  * scoreboard: total / per-channel comparison counts, number of [CMPSUC] messages
  * time stamp of every scoreboard comparison and the end-of-test time
      case0 / case1 : deterministic -> must be identical to the oracle
      case2         : identical as long as the formatter driver's mailbox never throttles fmt_grant;
                      otherwise only the prefix before the first throttling is compared and the end
                      time must be within +-3 % (it then depends on the $urandom_range stream)
"""
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import mcdf_oracle as m

FIFO = {'SHORT_FIFO': 64, 'MED_FIFO': 256, 'LONG_FIFO': 512, 'ULTRA_FIFO': 2048}
BAND = {'LOW_WIDTH': 8, 'MED_WIDTH': 4, 'HIGH_WIDTH': 2, 'ULTRA_WIDTH': 1}


def num(tok):
    tok = tok.strip()
    if tok.startswith("'h"):
        return int(tok[2:], 16)
    if tok.startswith("'d"):
        return int(tok[2:])
    return int(tok, 0)


def parse(path):
    r = dict(mon=[], cmp=[], counts={}, total=None, errcnt=None, end=None, sev={}, passed=None,
             fmt=None, chan={}, failed=False)
    cur = None
    for line in open(path, encoding='utf-8', errors='replace'):
        line = line.rstrip('\r\n')
        mm = re.search(r'@ ([0-9.]+)\s*ns: \S+ \[monitor_bus\] monitored addr ([0-9a-fA-F]+), cmd\s+(\w+), data 0x([0-9a-fA-F]+)', line)
        if mm:
            r['mon'].append((int(float(mm.group(1))), int(mm.group(2), 16), mm.group(3), int(mm.group(4), 16)))
            continue
        mm = re.search(r'@ ([0-9.]+)\s*ns: \S+ \[\[CMPSUC\]\] (\d+)th', line)
        if mm:
            r['cmp'].append(int(float(mm.group(1))))
            continue
        if '[[CMPERR]]' in line or '[[CHKERR]]' in line:
            r['failed'] = True
        mm = re.search(r'@ ([0-9.]+)\s*ns: \S+ \[scoreboard_mcdf\]', line)
        if mm:
            r['end'] = int(float(mm.group(1)))
        mm = re.search(r'total comparison count:\s*(\d+)', line)
        if mm:
            r['total'] = int(mm.group(1))
        mm = re.search(r'channel\[(\d)\] comparison count:\s*(\d+)', line)
        if mm:
            r['counts'][int(mm.group(1))] = int(mm.group(2))
        mm = re.search(r'total error count:\s*(\d+)', line)
        if mm:
            r['errcnt'] = int(mm.group(1))
        mm = re.match(r'\s*(UVM_INFO|UVM_WARNING|UVM_ERROR|UVM_FATAL)\s*:\s*(\d+)', line)
        if mm:
            r['sev'][mm.group(1)] = int(mm.group(2))
        mm = re.search(r'TestCase Passed: (\w+)', line)
        if mm:
            r['passed'] = mm.group(1)
        if 'TestCase Failed' in line:
            r['failed'] = True
        # uvm_printer tables of the randomised sequences
        if line.startswith(('UVM_INFO ', 'UVM_WARNING ', 'UVM_ERROR ', 'UVM_FATAL ')):
            cur = None                              # a new message ends the table of the previous one
            if '[sequence_formatter] After randomization' in line:
                cur = ('fmt', {})
            elif line.rstrip().endswith('[sequence_channel]'):
                cur = ('chan', {})                  # post_randomize(): its text starts on the next line
        mm = re.match(r'\s+(fifo|bandwidth)\s+\S+\s+\d+\s+(\w+)', line)
        if mm and cur and cur[0] == 'fmt':
            cur[1][mm.group(1)] = mm.group(2)
            if len(cur[1]) == 2:
                r['fmt'] = (FIFO[cur[1]['fifo']], BAND[cur[1]['bandwidth']])
        mm = re.match(r"\s+(ch_id|data_nidles|pkt_nidles|data_size|ntrans)\s+integral\s+\d+\s+('h[0-9a-fA-F]+|'d\d+|\d+)", line)
        if mm and cur and cur[0] == 'chan':
            cur[1][mm.group(1)] = num(mm.group(2))
            if len(cur[1]) == 5:
                d = cur[1]
                r['chan'][d['ch_id']] = (d['ntrans'], d['data_size'], d['data_nidles'], d['pkt_nidles'])
    return r


def main():
    name, path = sys.argv[1], sys.argv[2]
    log = parse(path)
    ok = True

    def check(cond, msg):
        nonlocal ok
        print('  [%s] %s' % ('PASS' if cond else 'FAIL', msg))
        ok = ok and cond

    override = None
    if name == 'case2':
        vals = [7, 7, 7]
        for (_, addr, cmd, data) in log['mon']:
            if cmd == 'write' and addr in (0, 4, 8):
                vals[addr // 4] = data
        check(log['fmt'] is not None and len(log['chan']) == 3, 'case2 parameters found in log: fmt(bound,period)=%s channels=%s regs=%s'
              % (log['fmt'], log['chan'], [hex(v) for v in vals]))
        if not ok:
            return 1
        override = dict(vals=vals, fmt=log['fmt'], params=[log['chan'][i] for i in range(3)])
        for i in range(3):
            nt, ds, dn, pn = log['chan'][i]
            check(400 <= nt <= 600 and ds in (8, 16, 32) and 0 <= dn <= 3 and pn in (1, 2, 4, 8),
                  'ch%d randomisation inside the inline constraints (ntrans=%d size=%d data_nidles=%d pkt_nidles=%d)' % (i, nt, ds, dn, pn))
            check(vals[i] & 1 == 1 and (vals[i] >> 3) in (0, 1), 'ch%d ctrl value 0x%02x: en=1, len in {4,8}' % (i, vals[i]))
    res, sim = m.run_test(name, 1, quiet=True, override=override)
    omon = []
    for (t, msg) in sim.log:
        mm = re.search(r'monitored addr ([0-9a-f]+), cmd (\w+), data 0x([0-9a-f]+)', msg)
        if mm:
            omon.append((t, int(mm.group(1), 16), mm.group(2), int(mm.group(3), 16)))
    ocmp = [m.tpos(s) + 10 * (L + 1) for (s, ch, L, d) in sim.out_pkts]

    check(log['passed'] == name and not log['failed'], '"TestCase Passed: %s" printed, no [CMPERR]/[CHKERR]' % name)
    check(log['sev'].get('UVM_ERROR') == 0 and log['sev'].get('UVM_FATAL') == 0,
          'UVM_ERROR=%s UVM_FATAL=%s (UVM_WARNING=%s; VCS is expected to show 1: predict-while-busy at 10 ns)'
          % (log['sev'].get('UVM_ERROR'), log['sev'].get('UVM_FATAL'), log['sev'].get('UVM_WARNING')))
    check(log['mon'] == omon, 'monitor_bus transfers identical to oracle (%d transfers: %s)'
          % (len(omon), ' '.join('%d:%s@%02x=%x' % (t, c[0].upper(), a, d) for (t, a, c, d) in omon)))
    if log['mon'] != omon:
        print('        log   :', log['mon'])
        print('        oracle:', omon)
    exp_counts = res['expected_pkts']
    got = [log['counts'].get(i) for i in range(3)]
    check(got == exp_counts and log['total'] == sum(exp_counts) and log['errcnt'] == 0,
          'comparison counts %s total %s errors %s  (oracle %s total %d)' % (got, log['total'], log['errcnt'], exp_counts, sum(exp_counts)))
    check(len(log['cmp']) == sum(exp_counts), 'number of [CMPSUC] messages = %d' % len(log['cmp']))
    lens = res['lens']
    deterministic = (name != 'case2') or (res['max_mailbox'] + max(lens) <= res['fifo_bound'])
    n = 0
    for a, b in zip(log['cmp'], ocmp):
        if a != b:
            break
        n += 1
    if deterministic:
        check(log['cmp'] == ocmp, 'all %d comparison time stamps identical to oracle (first %s ns, last %s ns)'
              % (len(ocmp), ocmp[0] if ocmp else None, ocmp[-1] if ocmp else None))
        check(log['end'] == res['end'], 'end of test at %s ns (oracle %d ns)' % (log['end'], res['end']))
    else:
        check(n >= 1 and log['cmp'][:1] == ocmp[:1], 'first comparison at %s ns (oracle %s ns); identical prefix of %d comparisons '
              '(then fmt_grant is throttled by the %d-word mailbox, timing depends on $urandom_range)'
              % (log['cmp'][:1], ocmp[:1], n, res['fifo_bound']))
        tol = 0.03 * res['end']
        check(log['end'] is not None and abs(log['end'] - res['end']) <= tol, 'end of test at %s ns within 3%% of oracle estimate %d ns'
              % (log['end'], res['end']))
    if log['cmp'] and log['end']:
        check(log['end'] - log['cmp'][-1] >= 1000, 'last comparison %d ns before the end (drain margin)' % (log['end'] - log['cmp'][-1]))
    print('RESULT: %s' % ('log matches the oracle' if ok else 'MISMATCH'))
    return 0 if ok else 1


if __name__ == '__main__':
    sys.exit(main())
