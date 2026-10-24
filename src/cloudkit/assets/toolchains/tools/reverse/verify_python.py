#!/usr/bin/env python3
"""Validate binary Python libraries on the generated benign ELF fixture."""
import argparse,hashlib,json
from importlib.metadata import version
from pathlib import Path
import capstone,lief,yara

parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--binary',type=Path,required=True);parser.add_argument('--output',type=Path,required=True)
args=parser.parse_args();data=args.binary.read_bytes()
distribution_versions={name:version(name) for name in ('lief','capstone','yara-python')}
assert distribution_versions=={'lief':'1.0.0','capstone':'5.0.9','yara-python':'4.5.4'},distribution_versions
parsed=lief.parse(str(args.binary));assert isinstance(parsed,lief.ELF.Binary)
symbol=parsed.get_symbol('fixture_sum');assert symbol is not None
code=bytes(parsed.get_content_from_virtual_address(symbol.value,symbol.size))
instructions=list(capstone.Cs(capstone.CS_ARCH_X86,capstone.CS_MODE_64).disasm(code,symbol.value))
assert instructions and any(item.mnemonic=='ret' for item in instructions)
rule=yara.compile(source='rule benign_fixture_marker { strings: $marker="fixture=" condition: $marker }')
assert len(rule.match(data=data))==1 and not rule.match(data=b'ordinary negative fixture')
report={'passed':True,'scope':'LIEF ELF metadata, Capstone static function disassembly and YARA benign literal marker; no input execution or malicious target',
 'input_sha256':hashlib.sha256(data).hexdigest(),
 'distribution_versions':distribution_versions,
 'module_versions':{'lief':lief.__version__,'capstone':capstone.__version__,'yara-python':yara.__version__,'capstone_core':capstone.cs_version()},
 'version_note':'Distribution lock versions are checked separately from upstream embedded module/core strings, which may retain an older value or build suffix.',
 'checks':{'elf_parse':True,'symbol_lookup':True,'static_disassembly_ret':True,'yara_positive':True,'yara_negative':True},
 'instructions':[{'address':item.address,'mnemonic':item.mnemonic,'operands':item.op_str} for item in instructions]}
args.output.parent.mkdir(parents=True,exist_ok=True);args.output.write_text(json.dumps(report,indent=2)+'\n');print(json.dumps(report))
