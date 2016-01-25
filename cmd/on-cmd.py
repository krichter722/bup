#!/bin/sh
"""": # -*-python-*-
bup_python="$(dirname "$0")/bup-python" || exit $?
exec "$bup_python" "$0" ${1+"$@"}
"""
# end of bup preamble
import sys, os, struct, getopt, subprocess, signal
from subprocess import PIPE
from bup.lib.bup import options, ssh, path
from bup.lib.bup.helpers import *

optspec = """
bup on <hostname> index ...
bup on <hostname> save ...
bup on <hostname> split ...
"""
o = options.Options(optspec, optfunc=getopt.getopt)
(opt, flags, extra) = o.parse(sys.argv[1:])
if len(extra) < 2:
    o.fatal('arguments expected')

class SigException(Exception):
    def __init__(self, signum):
        self.signum = signum
        Exception.__init__(self, 'signal %d received' % signum)
def handler(signum, frame):
    raise SigException(signum)

signal.signal(signal.SIGTERM, handler)
signal.signal(signal.SIGINT, handler)

try:
    sp = None
    p = None
    ret = 99

    hp = extra[0].split(':')
    if len(hp) == 1:
        (hostname, port) = (hp[0], None)
    else:
        (hostname, port) = hp
    argv = extra[1:]
    p = ssh.connect(hostname, port, 'on--server', stderr=PIPE)

    try:
        argvs = '\0'.join(['bup'] + argv)
        p.stdin.write(struct.pack('!I', len(argvs)) + argvs)
        p.stdin.flush()
        sp = subprocess.Popen([path.exe(), 'server'],
                              stdin=p.stdout, stdout=p.stdin)
        p.stdin.close()
        p.stdout.close()
        # Demultiplex remote client's stderr (back to stdout/stderr).
        dmc = DemuxConn(p.stderr.fileno(), open(os.devnull, "w"))
        for line in iter(dmc.readline, ""):
            sys.stdout.write(line)
    finally:
        while 1:
            # if we get a signal while waiting, we have to keep waiting, just
            # in case our child doesn't die.
            try:
                ret = p.wait()
                if sp:
                    sp.wait()
                break
            except SigException as e:
                log('\nbup on: %s\n' % e)
                os.kill(p.pid, e.signum)
                ret = 84
except SigException as e:
    if ret == 0:
        ret = 99
    log('\nbup on: %s\n' % e)

sys.exit(ret)
