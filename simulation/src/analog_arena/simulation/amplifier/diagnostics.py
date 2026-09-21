"""Bounded syntax diagnostics; no circuit repair or design defaults."""
from __future__ import annotations

from analog_arena.simulation.spice import StructuralError

MOS_FORMAT = 'Xname D G S B model W=<number_um> L=<number_um> M=<integer>'
MODELS = ['sky130_fd_pr__nfet_01v8', 'sky130_fd_pr__pfet_01v8']


def diagnostic(message, line_number=None, source=''):
    tokens = source.split()
    name = tokens[0] if tokens else None
    result = {'code': 'netlist_syntax', 'message': message[:400]}
    if line_number is not None:
        result['line'] = line_number
    if name and not name.startswith('.'):
        result['element'] = name[:80]
    if message.startswith('only SKY130'):
        result.update(code='mos_model_or_field_position', expected=MOS_FORMAT,
                      allowed_models=MODELS,
                      hint='Check the four terminal fields and model position; no connection is inferred.')
    elif message.startswith('invalid MOS group'):
        result.update(code='mos_fields', expected=MOS_FORMAT,
                      hint='Provide four terminals, one allowed model, and separate W=, L=, M= fields.')
    elif 'MOS parameter' in message or message.startswith(('each MOS', 'W, L, and M', 'wrapper mult')):
        result.update(code='mos_parameters', expected='W=<number_um> L=<number_um> M=<integer>')
    elif 'outside' in message or message.startswith('M must be'):
        result['code'] = 'parameter_range'
    elif message.startswith('duplicate element'):
        result['code'] = 'duplicate_element'
    elif message.startswith('capacitor must'):
        result.update(code='capacitor_fields', expected='Cname node_a node_b <value>p')
    elif message.startswith('resistor must'):
        result.update(code='resistor_fields', expected='Rname node_a node_b <value>k')
    elif message.startswith('forbidden'):
        result.update(code='element_or_directive', hint='DUT body accepts X MOS groups, R and C lines; comments start with *.')
    elif message.startswith('DUT is empty') or message.startswith('DUT must contain'):
        result.update(code='empty_dut', hint='Comments and subcircuit declarations are not devices. Submit the complete DUT.')
    elif message.startswith('subcircuit must'):
        result['code'] = 'subcircuit_header'
    elif message.startswith('DUT must end'):
        result.update(code='subcircuit_end', expected='.ends OTA')
    elif message.startswith('IBIAS must'):
        result['code'] = 'unused_ibias'
    if name and name.lower().startswith('x'):
        fields = ('drain', 'gate', 'source', 'body', 'model')
        result['parsed'] = {field: tokens[i][:100] if len(tokens) > i else None
                            for i, field in enumerate(fields, 1)}
        result['parameters'] = [token[:100] for token in tokens[6:14]]
    return result


class NetlistDiagnosticError(StructuralError):
    def __init__(self, diagnostics, truncated=False):
        self.diagnostics = diagnostics
        self.diagnostics_truncated = truncated
        messages = []
        for item in diagnostics:
            location = f"line {item['line']}" if 'line' in item else 'DUT'
            if item.get('element'):
                location += f" ({item['element']})"
            messages.append(f"{location}: {item['message']}")
        super().__init__('; '.join(messages))
