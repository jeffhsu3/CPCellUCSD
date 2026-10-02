"""Hard constraint semantics and boundary-port routing/export regressions."""

import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

import klayout.db as pya
from ortools.sat.python import cp_model

from cpcell.core import constraints, routing
from cpcell.core.finfet import FinFET
from cpcell.gds.gds_GT2N_SH import GT2NLayout
from cpcell.gds.gds_FinFET_SH import FinFETLayout
from cpcell.gds.result import parse_boundary_ports, parse_result
from cpcell.tech.tech import FinFET_Tech
from cpcell.utility import config
from cpcell.utility.entity import LayerStack
from cpcell.utility.util import read_cdl_file

ROOT = Path(__file__).resolve().parents[1]
LAYER = ROOT / 'input/config/GT2N_FinFET_2F_4T_4242OF0.layer'


def model(ports=(), placement=()):
    config.init()
    cell = FinFET.__new__(FinFET)
    cell.circuit = read_cdl_file(ROOT / 'tests/fixtures/sram6t.cdl')[0]
    cell.fin_tech = FinFET_Tech('test', 2, 4, 46, LayerStack(str(LAYER)))
    cell.num_col = 13
    cell.mos_to_num_finger = {}
    cell.cell_config = {'boundary_ports': {'value': list(ports)}, 'placement_constraints': {'value': list(placement)}}
    cell._init_graph()
    cell._init_tech()
    cell._init_CP_domain()
    constraints.read_options(cell)
    cell.opt = cp_model.CpModel()
    cell.opt.log_comment = lambda *_: None
    cell.transistor_vars = {}
    cell.placed_tran_ci_vars = {}
    cell.has_tran_at_ci_vars = {}
    cell._init_transistor_vars()
    cell._init_cpp()
    return cell


def port(name='wl', net='WL', side='left', layer='M2', **kwargs):
    return dict(name=name, net=net, side=side, layer=layer, **kwargs)


class ConstraintTest(unittest.TestCase):
    def test_fixed_order_alignment_and_mirror(self):
        cell = model(placement=[
            {'type': 'fixed', 'transistor': 'MP0S0', 'column': 1, 'row': 2, 'flip': False},
            {'type': 'fixed', 'transistor': 'MP1S0', 'columns': [7, 9]},
            {'type': 'order', 'transistors': ['MP0S0', 'MP1S0'], 'min_spacing': 4},
            {'type': 'align', 'transistors': ['MP0S0', 'MN0S0']},
            {'type': 'mirror', 'transistors': ['MP0S0', 'MP1S0']},
        ])
        constraints.apply_placement(cell)
        solver = cp_model.CpSolver()
        self.assertEqual(solver.Solve(cell.opt), cp_model.OPTIMAL)
        values = cell.transistor_vars
        self.assertEqual(solver.Value(values['MP1S0'].x_var), 9)
        self.assertEqual(solver.Value(values['MN0S0'].x_var), 1)
        self.assertEqual(solver.Value(values['MP1S0'].flip_var), 1)
        self.assertEqual(solver.Value(cell.cpp_cost), 9)
        # The constraint is hard, not an objective preference.
        cell.opt.Add(values['MP1S0'].flip_var == 0)
        self.assertEqual(solver.Solve(cell.opt), cp_model.INFEASIBLE)

    def test_invalid_schema_names_and_domains(self):
        bad_ports = [
            port(net='VDD'), port(net='missing'), port(side='up'),
            port(side='top'), port(layer='PC'), port(track=True),
            port(track=-1), port(track=100), port(typo=1),
        ]
        for spec in bad_ports:
            with self.subTest(port=spec), self.assertRaises(ValueError):
                model(ports=[spec])
        with self.assertRaisesRegex(ValueError, 'unique'):
            model(ports=[port(), port()])
        bad_rules = [
            {'type': 'fixed', 'transistor': 'MP0', 'column': 1},
            {'type': 'fixed', 'transistor': 'MP0S0', 'column': 2},
            {'type': 'fixed', 'transistor': 'MP0S0', 'row': 0},
            {'type': 'fixed', 'transistor': 'MP0S0', 'flip': 1},
            {'type': 'fixed', 'transistor': 'MP0S0', 'columns': []},
            {'type': 'order', 'transistors': ['MP0S0', 'MP1S0'], 'min_spacing': 0},
            {'type': 'align', 'transistors': ['MP0S0', 'MP0S0']},
            {'type': 'mirror', 'transistors': ['MP0S0']},
            {'type': 'unknown'},
        ]
        for rule in bad_rules:
            with self.subTest(rule=rule), self.assertRaises(ValueError):
                model(placement=[rule])

    def test_right_edge_tracks_solved_width_and_multiple_flow_counts(self):
        spec = port(side='right', track=2)
        for width in (7, 9):
            cell = model(ports=[spec, port(name='wl_left', track=2)])
            cell.num_pins_for_io = 0
            cell.net_flow_vars, cell.net_to_flow_cnt = {}, {}
            cell._init_net_flow_vars()
            self.assertEqual(cell.net_to_flow_cnt['WL'], cell.circuit.nets['WL'].num_terminals() + 2)
            self.assertEqual(cell.net_to_flow_cnt['BL'], cell.circuit.nets['BL'].num_terminals() + 1)
            cell.son_terminal_nodes = {}
            cell._init_SON_positions()
            cell.node_is_SON_vars, cell.node_to_net_SON_vars = {}, {}
            cell._init_SON_vars()
            routing.net_SON_node_uniqueness(cell)
            cell.opt.Add(cell.cpp_cost == width)
            solver = cp_model.CpSolver()
            self.assertEqual(solver.Solve(cell.opt), cp_model.OPTIMAL)
            pins = cell.node_is_SON_vars['WL'][cell.circuit.nets['WL'].num_terminals()]
            selected = [node for node, var in pins.items() if solver.Value(var)]
            edge = max(col for col in cell.lgg.cols_in_layer('M2') if col <= (width + 2) * 42)
            self.assertEqual(selected, [(3, 96, edge)])

    def test_duplicate_attachment_is_infeasible(self):
        cell = model(ports=[port(track=1), port(name='second', track=1)])
        cell.net_to_flow_cnt = {
            net.name: net.num_terminals() + (len(cell.boundary_ports.get(net.name, [None])) if net.is_io_net() else 0)
            for net in cell.circuit.get_nets(with_power_ground=False)
        }
        cell.son_terminal_nodes = {}
        cell._init_SON_positions()
        cell.node_is_SON_vars, cell.node_to_net_SON_vars = {}, {}
        cell._init_SON_vars()
        routing.net_SON_node_uniqueness(cell)
        self.assertEqual(cp_model.CpSolver().Solve(cell.opt), cp_model.INFEASIBLE)

    def test_route_fixed_placement_and_export_four_boundary_ports(self):
        with tempfile.TemporaryDirectory() as directory:
            out = Path(directory)
            config.generate_config(4, 'FinFET', 'SH', ['INV_X1'], out)
            path = out / 'config/INV_X1.json'
            settings = json.loads(path.read_text())
            for key, value in {'enforce_diffusion_alignment': False, 'insert_num_db': 2, 'num_search_workers': 1}.items():
                settings[key]['value'] = value
            settings['max_time'] = {'value': True, 'time': 15}
            settings['boundary_ports']['value'] = [
                port(name='i_' + side, net='I', side=side, track=1) for side in ('left', 'right')
            ] + [
                port(name='zn_' + side, net='ZN', side=side, layer='M1', track=2) for side in ('top', 'bottom')
            ]
            settings['placement_constraints']['value'] = [
                {'type': 'fixed', 'transistor': 'MM0S0', 'column': 3, 'flip': False},
                {'type': 'align', 'transistors': ['MM0S0', 'MM1S0']},
            ]
            path.write_text(json.dumps(settings))
            run = subprocess.run([
                sys.executable, '-m', 'cpcell', '--layer', str(LAYER),
                '--netlist', str(ROOT / 'input/cdl/PROBE3_2F4T.cdl'), '--cell_names', 'INV_X1',
                '--cell_config', str(path), '--output_dir', str(out), '--flag_log_constraints', 'False',
            ], cwd=ROOT, env=dict(os.environ, PYTHONDONTWRITEBYTECODE='1', MPLCONFIGDIR=str(out / 'mpl')),
                capture_output=True, text=True, timeout=45)
            self.assertEqual(run.returncode, 0, run.stdout + run.stderr)
            result = out / 'result/INV_X1.res'
            tech, pmos, nmos, routes = parse_result(result)
            self.assertEqual((pmos[0].x, nmos[0].x, nmos[0].flip), (126, 126, False))
            ports = parse_boundary_ports(result)
            self.assertEqual(len(ports), 4)
            self.assertEqual({(p['name'], p['row'], p['col']) for p in ports}, {
                ('i_left', 48, 0), ('i_right', 48, 168), ('zn_top', 144, 168), ('zn_bottom', 0, 168),
            })
            gds = out / 'cell.gds'
            GT2NLayout(result, 'INV_PORTS', gds)
            layout = pya.Layout()
            layout.read(str(gds))
            cell = layout.cell('INV_PORTS')
            # Datatype 251 pin metal reaches the physical cell boundary in nm.
            for layer, expected in ((30, pya.Box(0, 108, tech.col * 84, 132)), (25, pya.Box(154, 0, 182, 288))):
                pins = pya.Region(cell.shapes(layout.layer(layer, 251))).merged()
                self.assertTrue((pya.Region(expected) - pins).is_empty())
                if layer == 30:
                    self.assertEqual(pins.bbox(), expected)
            with self.assertRaisesRegex(ValueError, 'GT2N'):
                FinFETLayout(result, 'UNSUPPORTED', out / 'legacy.gds')
            # Reject disconnected metadata instead of drawing a floating pin.
            result.write_text(result.read_text().replace('"col": 168, "layer": "M1"', '"col": 42, "layer": "M1"'))
            before = gds.read_bytes()
            with self.assertRaisesRegex(ValueError, 'not attached'):
                GT2NLayout(result, 'INV_PORTS', gds)
            self.assertEqual(gds.read_bytes(), before)

    def test_unequal_sram_routes_six_ports(self):
        with tempfile.TemporaryDirectory() as directory:
            out = Path(directory)
            config.generate_config(4, 'FinFET', 'SH', ['SRAM6T'], out)
            path = out / 'config/SRAM6T.json'
            settings = json.loads(path.read_text())
            settings.update(json.loads((ROOT / 'tests/fixtures/sram6t_constraints.json').read_text()))
            settings['num_search_workers']['value'] = 4
            settings['max_time'] = {'value': True, 'time': 30}
            settings['use_relative_gap'] = {'value': True, 'perc': 0.05}
            path.write_text(json.dumps(settings))
            run = subprocess.run([
                sys.executable, '-m', 'cpcell', '--layer', str(LAYER),
                '--netlist', str(ROOT / 'tests/fixtures/sram6t.cdl'), '--cell_names', 'SRAM6T',
                '--cell_config', str(path), '--output_dir', str(out), '--flag_log_constraints', 'False',
            ], cwd=ROOT, env=dict(os.environ, PYTHONDONTWRITEBYTECODE='1', MPLCONFIGDIR=str(out / 'mpl')),
                capture_output=True, text=True, timeout=60)
            self.assertEqual(run.returncode, 0, run.stdout + run.stderr)
            result = out / 'result/SRAM6T.res'
            ports = parse_boundary_ports(result)
            self.assertEqual(len(ports), 6)
            _, pmos, nmos, routes = parse_result(result)
            self.assertEqual((len(pmos), len(nmos)), (2, 4))
            positions = {tran.name: tran.x for tran in pmos + nmos}
            self.assertLess(positions['MA0S0'], positions['MA1S0'])
            for p in ports:
                self.assertEqual(p['row'] if p['net'] == 'WL' else p['col'],
                                 {'WL': 0, 'BL': 84, 'BLB': 420}[p['net']])
            # Every PC segment must have a real terminal in each row it
            # touches, including vacancies in independently placed rows.
            terminals = {}
            for row, devices in ((0, nmos), (1, pmos)):
                for tran in devices:
                    for col, net in ((tran.source_col, tran.source_net),
                                     (tran.drain_col, tran.drain_net),
                                     (tran.gate_col, tran.gate_net)):
                        if col >= 0:
                            terminals.setdefault((row, col), set()).add(net)
            for route in routes:
                for layer, row, col in ((route.metal_0, route.row_0, route.col_0),
                                        (route.metal_1, route.row_1, route.col_1)):
                    if layer == 0:
                        self.assertIn(route.net, terminals.get((int(row >= 96), col), set()))


if __name__ == '__main__':
    unittest.main()
