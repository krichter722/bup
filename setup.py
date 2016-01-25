#!/usr/bin/python
# -*- coding: utf-8 -*- 

from setuptools import setup, find_packages, Extension
from pkg_resources import parse_version
import bup.bup_globals as bup_globals

module1 = Extension('bup.lib.bup._helpers',
                    sources = ["bup/lib/bup/bupsplit.c", 'bup/lib/bup/_helpers.c'])

setup(
    name = bup_globals.app_name,
    version_command=('git describe --tags', "pep440-git"),
    packages = find_packages(),
    setup_requires = ["setuptools-version-command>=2.2"],
    install_requires = ["cheetah", "plac>=0.9.1"],
    ext_modules = [module1],
    entry_points={
        'console_scripts': [
            '%s = bup.bup:main' % (bup_globals.app_name, ),
        ],
    },
)
