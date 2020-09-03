#!/bin/sh
"""": # -*-python-*-
# https://sourceware.org/bugzilla/show_bug.cgi?id=26034
export "BUP_ARGV_0"="$0"
arg_i=1
for arg in "$@"; do
    export "BUP_ARGV_${arg_i}"="$arg"
    shift
    arg_i=$((arg_i + 1))
done
# Here to end of preamble replaced during install
bup_python="$(dirname "$0")/../../config/bin/python" || exit $?
exec "$bup_python" "$0"
"""
# end of bup preamble

from __future__ import absolute_import
import glob, os, sys, tempfile
import threading
import concurrent.futures.ThreadPoolExecutor as ThreadPoolExecutor

sys.path[:0] = [os.path.dirname(os.path.realpath(__file__)) + '/..']

from bup import compat, options, git, bloom
from bup.compat import argv_bytes, hexstr
from bup.helpers import (add_error, debug1, handle_ctrl_c, log, progress, qprogress,
                         saved_errors)
from bup.io import path_msg


optspec = """
bup bloom [options...]
--
ruin       ruin the specified bloom file (clearing the bitfield)
f,force    ignore existing bloom file and regenerate it from scratch
o,output=  output bloom filename (default: auto)
d,dir=     input directory to look for idx files (default: auto)
k,hashes=  number of hash functions to use (4 or 5) (default: auto)
c,check=   check the given .idx file against the bloom filter
"""


def ruin_bloom(bloomfilename):
    rbloomfilename = git.repo_rel(bloomfilename)
    if not os.path.exists(bloomfilename):
        log(path_msg(bloomfilename) + '\n')
        add_error('bloom: %s not found to ruin\n' % path_msg(rbloomfilename))
        return
    b = bloom.ShaBloom(bloomfilename, readwrite=True, expected=1)
    b.map[16 : 16 + 2**b.bits] = b'\0' * 2**b.bits


def check_bloom(path, bloomfilename, idx):
    rbloomfilename = git.repo_rel(bloomfilename)
    ridx = git.repo_rel(idx)
    if not os.path.exists(bloomfilename):
        log('bloom: %s: does not exist.\n' % path_msg(rbloomfilename))
        return
    b = bloom.ShaBloom(bloomfilename)
    if not b.valid():
        add_error('bloom: %r is invalid.\n' % path_msg(rbloomfilename))
        return
    base = os.path.basename(idx)
    if base not in b.idxnames:
        log('bloom: %s does not contain the idx.\n' % path_msg(rbloomfilename))
        return
    if base == idx:
        idx = os.path.join(path, idx)
    log('bloom: bloom file: %s\n' % path_msg(rbloomfilename))
    log('bloom:   checking %s\n' % path_msg(ridx))
    for objsha in git.open_idx(idx):
        if not b.exists(objsha):
            add_error('bloom: ERROR: object %s missing' % hexstr(objsha))


_first = None
def do_bloom(path, outfilename, k):
    global _first
    assert k in (None, 4, 5)
    b = None
    if os.path.exists(outfilename) and not opt.force:
        b = bloom.ShaBloom(outfilename)
        if not b.valid():
            debug1("bloom: Existing invalid bloom found, regenerating.\n")
            b = None

    add = []
    rest = []
    add_count = AtomicInteger()
    rest_count = AtomicInteger()

    def __bloom_enumerate__(i, name):
        ix = git.open_idx(name)
        ixbase = os.path.basename(name)
        if b and (ixbase in b.idxnames):
            rest.append(name)
            rest_count.inc(len(ix))
        else:
            add.append(name)
            add_count.inc(len(ix))

    with ThreadPoolExecutor(max_workers=16) as executor:
        for i, name in enumerate(glob.glob(b'%s/*.idx' % path)):
            executor.submit(__bloom_enumerate__, i, name)

    if not add:
        debug1("bloom: nothing to do.\n")
        return

    if b:
        if len(b) != rest_count.value:
            debug1("bloom: size %d != idx total %d, regenerating\n"
                   % (len(b), rest_count.value))
            b = None
        elif k is not None and k != b.k:
            debug1("bloom: new k %d != existing k %d, regenerating\n"
                   % (k, b.k))
            b = None
        elif (b.bits < bloom.MAX_BLOOM_BITS[b.k] and
              b.pfalse_positive(add_count.value) > bloom.MAX_PFALSE_POSITIVE):
            debug1("bloom: regenerating: adding %d entries gives "
                   "%.2f%% false positives.\n"
                   % (add_count.value, b.pfalse_positive(add_count.value)))
            b = None
        else:
            b = bloom.ShaBloom(outfilename, readwrite=True, expected=add_count.value)
    if not b: # Need all idxs to build from scratch
        add += rest
        add_count.inc(rest_count.value)
    #del rest
    #del rest_count

    msg = b is None and 'creating from' or 'adding'
    if not _first: _first = path
    dirprefix = (_first != path) and git.repo_rel(path) + b': ' or b''
    progress('bloom: %s%s %d file%s (%d object%s).\r'
        % (path_msg(dirprefix), msg,
           len(add), len(add)!=1 and 's' or '',
           add_count.value, add_count.value != 1 and 's' or ''))

    tfname = None
    if b is None:
        tfname = os.path.join(path, b'bup.tmp.bloom')
        b = bloom.create(tfname, expected=add_count.value, k=k)
    count = 0
    icount = 0
    for name in add:
        ix = git.open_idx(name)
        qprogress('bloom: writing %.2f%% (%d/%d objects)\r' 
                  % (icount*100.0/add_count.value, icount, add_count.value))
        b.add_idx(ix)
        count += 1
        icount += len(ix)

    # Currently, there's an open file object for tfname inside b.
    # Make sure it's closed before rename.
    b.close()

    if tfname:
        os.rename(tfname, outfilename)


class AtomicInteger():
    def __init__(self, value=0):
        self._value = value
        self._lock = threading.Lock()

    def inc(self, value=1):
        with self._lock:
            self._value += value
            return self._value

    def dec(self, value=1):
        with self._lock:
            self._value -= value
            return self._value


    @property
    def value(self):
        with self._lock:
            return self._value

    @value.setter
    def value(self, v):
        with self._lock:
            self._value = v
            return self._value


handle_ctrl_c()

o = options.Options(optspec)
opt, flags, extra = o.parse(compat.argv[1:])

if extra:
    o.fatal('no positional parameters expected')

if not opt.check and opt.k and opt.k not in (4,5):
    o.fatal('only k values of 4 and 5 are supported')

if opt.check:
    opt.check = argv_bytes(opt.check)

git.check_repo_or_die()

output = argv_bytes(opt.output) if opt.output else None
paths = opt.dir and [argv_bytes(opt.dir)] or git.all_packdirs()
for path in paths:
    debug1('bloom: scanning %s\n' % path_msg(path))
    outfilename = output or os.path.join(path, b'bup.bloom')
    if opt.check:
        check_bloom(path, outfilename, opt.check)
    elif opt.ruin:
        ruin_bloom(outfilename)
    else:
        do_bloom(path, outfilename, opt.k)

if saved_errors:
    log('WARNING: %d errors encountered during bloom.\n' % len(saved_errors))
    sys.exit(1)
elif opt.check:
    log('All tests passed.\n')
