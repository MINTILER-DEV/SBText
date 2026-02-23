from __future__ import annotations

import hashlib
import json
import re
import zipfile
from dataclasses import dataclass
from pathlib import Path
from xml.etree import ElementTree as ET

from parser import (
    AddToListStmt,
    BinaryExpr,
    BuiltinReporterExpr,
    ChangeSizeByStmt,
    ChangeXByStmt,
    ChangeYByStmt,
    BroadcastStmt,
    ChangeVarStmt,
    CostumeDecl,
    DeleteAllOfListStmt,
    DeleteOfListStmt,
    EventScript,
    Expr,
    ForeverStmt,
    GoToXYStmt,
    HideStmt,
    IfStmt,
    IfOnEdgeBounceStmt,
    InsertAtListStmt,
    KeyPressedExpr,
    ListContainsExpr,
    ListItemExpr,
    ListLengthExpr,
    MoveStmt,
    NextBackdropStmt,
    NextCostumeStmt,
    NumberExpr,
    PickRandomExpr,
    PointInDirectionStmt,
    Procedure,
    ProcedureCallStmt,
    Project,
    ReplaceItemOfListStmt,
    ResetTimerStmt,
    RepeatStmt,
    SetSizeToStmt,
    SetXStmt,
    SetYStmt,
    SayStmt,
    SetVarStmt,
    ShowStmt,
    Statement,
    StopStmt,
    StringExpr,
    Target,
    ThinkStmt,
    TurnLeftStmt,
    TurnRightStmt,
    UnaryExpr,
    VarExpr,
    WaitStmt,
    AskStmt,
)


class CodegenError(ValueError):
    """Raised when Scratch project generation fails."""


DEFAULT_STAGE_SVG = """<svg xmlns="http://www.w3.org/2000/svg" width="480" height="360" viewBox="0 0 480 360"><rect width="480" height="360" fill="#ffffff"/></svg>""".encode(
    "utf-8"
)
DEFAULT_SPRITE_SVG = """<svg xmlns="http://www.w3.org/2000/svg" width="96" height="96" viewBox="0 0 96 96"><circle cx="48" cy="48" r="40" fill="#4c97ff"/></svg>""".encode(
    "utf-8"
)
DEFAULT_SVG_TARGET_SIZE = 64.0


def generate_project_json(project: Project, source_dir: Path, scale_svgs: bool = True) -> tuple[dict, dict[str, bytes]]:
    builder = _ProjectBuilder(project=project, source_dir=source_dir, scale_svgs=scale_svgs)
    return builder.build()


def write_sb3(project_json: dict, assets: dict[str, bytes], output_path: Path) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(output_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("project.json", json.dumps(project_json, indent=2))
        for asset_name, asset_bytes in assets.items():
            zf.writestr(asset_name, asset_bytes)


@dataclass
class _ProcedureSignature:
    name: str
    params: list[str]
    arg_ids: list[str]
    proccode: str


class _ProjectBuilder:
    def __init__(self, project: Project, source_dir: Path, scale_svgs: bool) -> None:
        self.project = project
        self.source_dir = source_dir
        self.scale_svgs = scale_svgs
        self._id_counter = 0
        self.assets: dict[str, bytes] = {}
        self.broadcast_ids: dict[str, str] = {}

    def build(self) -> tuple[dict, dict[str, bytes]]:
        self.broadcast_ids = self._collect_broadcast_ids()
        ordered_targets = sorted(self.project.targets, key=lambda t: 0 if t.is_stage else 1)
        if not any(target.is_stage for target in ordered_targets):
            ordered_targets = [self._synthesized_stage_target(ordered_targets)] + ordered_targets
        targets_json: list[dict] = []
        sprite_layer = 1
        for target in ordered_targets:
            layer = 0 if target.is_stage else sprite_layer
            if not target.is_stage:
                sprite_layer += 1
            targets_json.append(self._build_target_json(target=target, layer_order=layer))
        project_json = {
            "targets": targets_json,
            "monitors": [],
            "extensions": [],
            "meta": {
                "semver": "3.0.0",
                "vm": "0.2.0",
                "agent": "SBText Compiler",
            },
        }
        return project_json, self.assets

    def _synthesized_stage_target(self, existing_targets: list[Target]) -> Target:
        existing_names = {target.name.lower() for target in existing_targets}
        stage_name = "Stage"
        suffix = 1
        while stage_name.lower() in existing_names:
            suffix += 1
            stage_name = f"Stage{suffix}"
        return Target(line=0, column=0, name=stage_name, is_stage=True)

    def _build_target_json(self, target: Target, layer_order: int) -> dict:
        blocks: dict[str, dict] = {}
        variables_map: dict[str, str] = {}
        variables_json: dict[str, list] = {}
        lists_map: dict[str, str] = {}
        lists_json: dict[str, list] = {}
        for var_decl in target.variables:
            var_id = self._new_id("var")
            variables_map[var_decl.name.lower()] = var_id
            variables_json[var_id] = [var_decl.name, 0]
        for list_decl in target.lists:
            list_id = self._new_id("list")
            lists_map[list_decl.name.lower()] = list_id
            lists_json[list_id] = [list_decl.name, []]

        procedures = self._build_procedure_signatures(target)
        y_cursor = 30
        for procedure in target.procedures:
            y_cursor = self._emit_procedure_definition(
                blocks=blocks,
                procedure=procedure,
                signatures=procedures,
                variables_map=variables_map,
                lists_map=lists_map,
                start_y=y_cursor,
            )
            y_cursor += 40

        for script in target.scripts:
            y_cursor = self._emit_event_script(
                blocks=blocks,
                script=script,
                signatures=procedures,
                variables_map=variables_map,
                lists_map=lists_map,
                start_y=y_cursor,
            )
            y_cursor += 40

        costumes_json = self._build_costumes(target)
        stage_broadcasts = {broadcast_id: message for message, broadcast_id in self.broadcast_ids.items()} if target.is_stage else {}

        target_json = {
            "isStage": target.is_stage,
            "name": target.name,
            "variables": variables_json,
            "lists": lists_json,
            "broadcasts": stage_broadcasts,
            "blocks": blocks,
            "comments": {},
            "currentCostume": 0,
            "costumes": costumes_json,
            "sounds": [],
            "volume": 100,
            "layerOrder": layer_order,
        }
        if target.is_stage:
            target_json.update(
                {
                    "tempo": 60,
                    "videoTransparency": 50,
                    "videoState": "on",
                    "textToSpeechLanguage": None,
                }
            )
        else:
            target_json.update(
                {
                    "visible": True,
                    "x": 0,
                    "y": 0,
                    "size": 100,
                    "direction": 90,
                    "draggable": False,
                    "rotationStyle": "all around",
                }
            )
        return target_json

    def _build_procedure_signatures(self, target: Target) -> dict[str, _ProcedureSignature]:
        signatures: dict[str, _ProcedureSignature] = {}
        for procedure in target.procedures:
            arg_ids = [self._new_id("arg") for _ in procedure.params]
            placeholders = " ".join("%s" for _ in procedure.params)
            proccode = procedure.name if not placeholders else f"{procedure.name} {placeholders}"
            signatures[procedure.name.lower()] = _ProcedureSignature(
                name=procedure.name,
                params=procedure.params,
                arg_ids=arg_ids,
                proccode=proccode,
            )
        return signatures

    def _emit_procedure_definition(
        self,
        blocks: dict[str, dict],
        procedure: Procedure,
        signatures: dict[str, _ProcedureSignature],
        variables_map: dict[str, str],
        lists_map: dict[str, str],
        start_y: int,
    ) -> int:
        signature = signatures[procedure.name.lower()]
        definition_id = self._new_block_id()
        prototype_id = self._new_block_id()
        blocks[definition_id] = {
            "opcode": "procedures_definition",
            "next": None,
            "parent": None,
            "inputs": {"custom_block": [1, prototype_id]},
            "fields": {},
            "shadow": False,
            "topLevel": True,
            "x": 30,
            "y": start_y,
        }

        prototype_inputs: dict[str, list] = {}
        for param_name, arg_id in zip(signature.params, signature.arg_ids):
            reporter_id = self._new_block_id()
            blocks[reporter_id] = {
                "opcode": "argument_reporter_string_number",
                "next": None,
                "parent": prototype_id,
                "inputs": {},
                "fields": {"VALUE": [param_name, None]},
                "shadow": True,
                "topLevel": False,
            }
            prototype_inputs[arg_id] = [1, reporter_id]

        blocks[prototype_id] = {
            "opcode": "procedures_prototype",
            "next": None,
            "parent": definition_id,
            "inputs": prototype_inputs,
            "fields": {},
            "shadow": True,
            "topLevel": False,
            "mutation": {
                "tagName": "mutation",
                "children": [],
                "proccode": signature.proccode,
                "argumentids": json.dumps(signature.arg_ids),
                "argumentnames": json.dumps(signature.params),
                "argumentdefaults": json.dumps(["" for _ in signature.params]),
                "warp": "false",
            },
        }

        first_stmt, last_stmt = self._emit_statement_chain(
            blocks=blocks,
            statements=procedure.body,
            parent_id=definition_id,
            variables_map=variables_map,
            lists_map=lists_map,
            signatures=signatures,
            param_scope=set(signature.params),
        )
        if first_stmt:
            blocks[definition_id]["next"] = first_stmt
            return start_y + 120 + (20 if last_stmt else 0)
        return start_y + 80

    def _emit_event_script(
        self,
        blocks: dict[str, dict],
        script: EventScript,
        signatures: dict[str, _ProcedureSignature],
        variables_map: dict[str, str],
        lists_map: dict[str, str],
        start_y: int,
    ) -> int:
        if script.event_type == "when_flag_clicked":
            opcode = "event_whenflagclicked"
            fields = {}
        elif script.event_type == "when_this_sprite_clicked":
            opcode = "event_whenthisspriteclicked"
            fields = {}
        elif script.event_type == "when_i_receive":
            opcode = "event_whenbroadcastreceived"
            if script.message is None:
                raise CodegenError("Internal error: missing message for when_i_receive event.")
            broadcast_id = self._broadcast_id(script.message)
            fields = {"BROADCAST_OPTION": [script.message, broadcast_id]}
        else:
            raise CodegenError(f"Unsupported event type '{script.event_type}'.")

        hat_id = self._new_block_id()
        blocks[hat_id] = {
            "opcode": opcode,
            "next": None,
            "parent": None,
            "inputs": {},
            "fields": fields,
            "shadow": False,
            "topLevel": True,
            "x": 320,
            "y": start_y,
        }

        first_stmt, last_stmt = self._emit_statement_chain(
            blocks=blocks,
            statements=script.body,
            parent_id=hat_id,
            variables_map=variables_map,
            lists_map=lists_map,
            signatures=signatures,
            param_scope=set(),
        )
        if first_stmt:
            blocks[hat_id]["next"] = first_stmt
            return start_y + 120 + (20 if last_stmt else 0)
        return start_y + 80

    def _emit_statement_chain(
        self,
        blocks: dict[str, dict],
        statements: list[Statement],
        parent_id: str,
        variables_map: dict[str, str],
        lists_map: dict[str, str],
        signatures: dict[str, _ProcedureSignature],
        param_scope: set[str],
    ) -> tuple[str | None, str | None]:
        first: str | None = None
        prev: str | None = None
        for stmt in statements:
            stmt_parent = parent_id if prev is None else prev
            stmt_id = self._emit_statement(
                blocks=blocks,
                stmt=stmt,
                parent_id=stmt_parent,
                variables_map=variables_map,
                lists_map=lists_map,
                signatures=signatures,
                param_scope=param_scope,
            )
            if prev is not None:
                blocks[prev]["next"] = stmt_id
            if first is None:
                first = stmt_id
            prev = stmt_id
        return first, prev

    def _emit_statement(
        self,
        blocks: dict[str, dict],
        stmt: Statement,
        parent_id: str,
        variables_map: dict[str, str],
        lists_map: dict[str, str],
        signatures: dict[str, _ProcedureSignature],
        param_scope: set[str],
    ) -> str:
        if isinstance(stmt, BroadcastStmt):
            return self._emit_broadcast_stmt(blocks, stmt, parent_id)
        if isinstance(stmt, SetVarStmt):
            return self._emit_set_stmt(blocks, stmt, parent_id, variables_map, lists_map, param_scope)
        if isinstance(stmt, ChangeVarStmt):
            return self._emit_change_stmt(blocks, stmt, parent_id, variables_map, lists_map, param_scope)
        if isinstance(stmt, MoveStmt):
            return self._emit_move_stmt(blocks, stmt, parent_id, variables_map, lists_map, param_scope)
        if isinstance(stmt, SayStmt):
            return self._emit_say_stmt(blocks, stmt, parent_id, variables_map, lists_map, param_scope)
        if isinstance(stmt, ThinkStmt):
            return self._emit_think_stmt(blocks, stmt, parent_id, variables_map, lists_map, param_scope)
        if isinstance(stmt, TurnRightStmt):
            return self._emit_turn_stmt(blocks, "motion_turnright", stmt.degrees, parent_id, variables_map, lists_map, param_scope)
        if isinstance(stmt, TurnLeftStmt):
            return self._emit_turn_stmt(blocks, "motion_turnleft", stmt.degrees, parent_id, variables_map, lists_map, param_scope)
        if isinstance(stmt, GoToXYStmt):
            return self._emit_go_to_xy_stmt(blocks, stmt, parent_id, variables_map, lists_map, param_scope)
        if isinstance(stmt, ChangeXByStmt):
            return self._emit_single_input_stmt(blocks, "motion_changexby", "DX", stmt.value, parent_id, variables_map, lists_map, param_scope, "number")
        if isinstance(stmt, SetXStmt):
            return self._emit_single_input_stmt(blocks, "motion_setx", "X", stmt.value, parent_id, variables_map, lists_map, param_scope, "number")
        if isinstance(stmt, ChangeYByStmt):
            return self._emit_single_input_stmt(blocks, "motion_changeyby", "DY", stmt.value, parent_id, variables_map, lists_map, param_scope, "number")
        if isinstance(stmt, SetYStmt):
            return self._emit_single_input_stmt(blocks, "motion_sety", "Y", stmt.value, parent_id, variables_map, lists_map, param_scope, "number")
        if isinstance(stmt, PointInDirectionStmt):
            return self._emit_single_input_stmt(blocks, "motion_pointindirection", "DIRECTION", stmt.direction, parent_id, variables_map, lists_map, param_scope, "number")
        if isinstance(stmt, IfOnEdgeBounceStmt):
            return self._emit_no_input_stmt(blocks, "motion_ifonedgebounce", parent_id)
        if isinstance(stmt, ChangeSizeByStmt):
            return self._emit_single_input_stmt(blocks, "looks_changesizeby", "CHANGE", stmt.value, parent_id, variables_map, lists_map, param_scope, "number")
        if isinstance(stmt, SetSizeToStmt):
            return self._emit_single_input_stmt(blocks, "looks_setsizeto", "SIZE", stmt.value, parent_id, variables_map, lists_map, param_scope, "number")
        if isinstance(stmt, ShowStmt):
            return self._emit_no_input_stmt(blocks, "looks_show", parent_id)
        if isinstance(stmt, HideStmt):
            return self._emit_no_input_stmt(blocks, "looks_hide", parent_id)
        if isinstance(stmt, NextCostumeStmt):
            return self._emit_no_input_stmt(blocks, "looks_nextcostume", parent_id)
        if isinstance(stmt, NextBackdropStmt):
            return self._emit_no_input_stmt(blocks, "looks_nextbackdrop", parent_id)
        if isinstance(stmt, WaitStmt):
            return self._emit_single_input_stmt(blocks, "control_wait", "DURATION", stmt.duration, parent_id, variables_map, lists_map, param_scope, "number")
        if isinstance(stmt, ForeverStmt):
            return self._emit_forever_stmt(blocks, stmt, parent_id, variables_map, lists_map, signatures, param_scope)
        if isinstance(stmt, StopStmt):
            return self._emit_stop_stmt(blocks, stmt, parent_id, variables_map, lists_map, param_scope)
        if isinstance(stmt, AskStmt):
            return self._emit_single_input_stmt(blocks, "sensing_askandwait", "QUESTION", stmt.question, parent_id, variables_map, lists_map, param_scope, "string")
        if isinstance(stmt, ResetTimerStmt):
            return self._emit_no_input_stmt(blocks, "sensing_resettimer", parent_id)
        if isinstance(stmt, AddToListStmt):
            return self._emit_add_to_list_stmt(blocks, stmt, parent_id, variables_map, lists_map, param_scope)
        if isinstance(stmt, DeleteOfListStmt):
            return self._emit_delete_of_list_stmt(blocks, stmt, parent_id, variables_map, lists_map, param_scope)
        if isinstance(stmt, DeleteAllOfListStmt):
            return self._emit_delete_all_of_list_stmt(blocks, stmt, parent_id, lists_map)
        if isinstance(stmt, InsertAtListStmt):
            return self._emit_insert_at_list_stmt(blocks, stmt, parent_id, variables_map, lists_map, param_scope)
        if isinstance(stmt, ReplaceItemOfListStmt):
            return self._emit_replace_item_of_list_stmt(blocks, stmt, parent_id, variables_map, lists_map, param_scope)
        if isinstance(stmt, RepeatStmt):
            return self._emit_repeat_stmt(blocks, stmt, parent_id, variables_map, lists_map, signatures, param_scope)
        if isinstance(stmt, IfStmt):
            return self._emit_if_stmt(blocks, stmt, parent_id, variables_map, lists_map, signatures, param_scope)
        if isinstance(stmt, ProcedureCallStmt):
            return self._emit_call_stmt(blocks, stmt, parent_id, variables_map, lists_map, signatures, param_scope)
        raise CodegenError(f"Unsupported statement type '{type(stmt).__name__}'.")

    def _emit_broadcast_stmt(self, blocks: dict[str, dict], stmt: BroadcastStmt, parent_id: str) -> str:
        block_id = self._new_block_id()
        menu_id = self._new_block_id()
        broadcast_id = self._broadcast_id(stmt.message)
        blocks[block_id] = {
            "opcode": "event_broadcast",
            "next": None,
            "parent": parent_id,
            "inputs": {"BROADCAST_INPUT": [1, menu_id]},
            "fields": {},
            "shadow": False,
            "topLevel": False,
        }
        blocks[menu_id] = {
            "opcode": "event_broadcast_menu",
            "next": None,
            "parent": block_id,
            "inputs": {},
            "fields": {"BROADCAST_OPTION": [stmt.message, broadcast_id]},
            "shadow": True,
            "topLevel": False,
        }
        return block_id

    def _emit_set_stmt(
        self,
        blocks: dict[str, dict],
        stmt: SetVarStmt,
        parent_id: str,
        variables_map: dict[str, str],
        lists_map: dict[str, str],
        param_scope: set[str],
    ) -> str:
        var_id = self._lookup_var_id(variables_map, stmt.var_name)
        block_id = self._new_block_id()
        blocks[block_id] = {
            "opcode": "data_setvariableto",
            "next": None,
            "parent": parent_id,
            "inputs": {
                "VALUE": self._expr_input(
                    blocks=blocks,
                    expr=stmt.value,
                    parent_id=block_id,
                    variables_map=variables_map,
                    lists_map=lists_map,
                    param_scope=param_scope,
                    default_kind="number",
                )
            },
            "fields": {"VARIABLE": [stmt.var_name, var_id]},
            "shadow": False,
            "topLevel": False,
        }
        return block_id

    def _emit_change_stmt(
        self,
        blocks: dict[str, dict],
        stmt: ChangeVarStmt,
        parent_id: str,
        variables_map: dict[str, str],
        lists_map: dict[str, str],
        param_scope: set[str],
    ) -> str:
        var_id = self._lookup_var_id(variables_map, stmt.var_name)
        block_id = self._new_block_id()
        blocks[block_id] = {
            "opcode": "data_changevariableby",
            "next": None,
            "parent": parent_id,
            "inputs": {
                "VALUE": self._expr_input(
                    blocks=blocks,
                    expr=stmt.delta,
                    parent_id=block_id,
                    variables_map=variables_map,
                    lists_map=lists_map,
                    param_scope=param_scope,
                    default_kind="number",
                )
            },
            "fields": {"VARIABLE": [stmt.var_name, var_id]},
            "shadow": False,
            "topLevel": False,
        }
        return block_id

    def _emit_move_stmt(
        self,
        blocks: dict[str, dict],
        stmt: MoveStmt,
        parent_id: str,
        variables_map: dict[str, str],
        lists_map: dict[str, str],
        param_scope: set[str],
    ) -> str:
        block_id = self._new_block_id()
        blocks[block_id] = {
            "opcode": "motion_movesteps",
            "next": None,
            "parent": parent_id,
            "inputs": {
                "STEPS": self._expr_input(
                    blocks=blocks,
                    expr=stmt.steps,
                    parent_id=block_id,
                    variables_map=variables_map,
                    lists_map=lists_map,
                    param_scope=param_scope,
                    default_kind="number",
                )
            },
            "fields": {},
            "shadow": False,
            "topLevel": False,
        }
        return block_id

    def _emit_say_stmt(
        self,
        blocks: dict[str, dict],
        stmt: SayStmt,
        parent_id: str,
        variables_map: dict[str, str],
        lists_map: dict[str, str],
        param_scope: set[str],
    ) -> str:
        block_id = self._new_block_id()
        blocks[block_id] = {
            "opcode": "looks_say",
            "next": None,
            "parent": parent_id,
            "inputs": {
                "MESSAGE": self._expr_input(
                    blocks=blocks,
                    expr=stmt.message,
                    parent_id=block_id,
                    variables_map=variables_map,
                    lists_map=lists_map,
                    param_scope=param_scope,
                    default_kind="string",
                )
            },
            "fields": {},
            "shadow": False,
            "topLevel": False,
        }
        return block_id

    def _emit_think_stmt(
        self,
        blocks: dict[str, dict],
        stmt: ThinkStmt,
        parent_id: str,
        variables_map: dict[str, str],
        lists_map: dict[str, str],
        param_scope: set[str],
    ) -> str:
        block_id = self._new_block_id()
        blocks[block_id] = {
            "opcode": "looks_think",
            "next": None,
            "parent": parent_id,
            "inputs": {
                "MESSAGE": self._expr_input(
                    blocks=blocks,
                    expr=stmt.message,
                    parent_id=block_id,
                    variables_map=variables_map,
                    lists_map=lists_map,
                    param_scope=param_scope,
                    default_kind="string",
                )
            },
            "fields": {},
            "shadow": False,
            "topLevel": False,
        }
        return block_id

    def _emit_turn_stmt(
        self,
        blocks: dict[str, dict],
        opcode: str,
        value: Expr,
        parent_id: str,
        variables_map: dict[str, str],
        lists_map: dict[str, str],
        param_scope: set[str],
    ) -> str:
        input_name = "DEGREES"
        return self._emit_single_input_stmt(
            blocks,
            opcode,
            input_name,
            value,
            parent_id,
            variables_map,
            lists_map,
            param_scope,
            "number",
        )

    def _emit_go_to_xy_stmt(
        self,
        blocks: dict[str, dict],
        stmt: GoToXYStmt,
        parent_id: str,
        variables_map: dict[str, str],
        lists_map: dict[str, str],
        param_scope: set[str],
    ) -> str:
        block_id = self._new_block_id()
        blocks[block_id] = {
            "opcode": "motion_gotoxy",
            "next": None,
            "parent": parent_id,
            "inputs": {},
            "fields": {},
            "shadow": False,
            "topLevel": False,
        }
        blocks[block_id]["inputs"]["X"] = self._expr_input(
            blocks=blocks,
            expr=stmt.x,
            parent_id=block_id,
            variables_map=variables_map,
            lists_map=lists_map,
            param_scope=param_scope,
            default_kind="number",
        )
        blocks[block_id]["inputs"]["Y"] = self._expr_input(
            blocks=blocks,
            expr=stmt.y,
            parent_id=block_id,
            variables_map=variables_map,
            lists_map=lists_map,
            param_scope=param_scope,
            default_kind="number",
        )
        return block_id

    def _emit_single_input_stmt(
        self,
        blocks: dict[str, dict],
        opcode: str,
        input_name: str,
        value: Expr,
        parent_id: str,
        variables_map: dict[str, str],
        lists_map: dict[str, str],
        param_scope: set[str],
        default_kind: str,
    ) -> str:
        block_id = self._new_block_id()
        blocks[block_id] = {
            "opcode": opcode,
            "next": None,
            "parent": parent_id,
            "inputs": {
                input_name: self._expr_input(
                    blocks=blocks,
                    expr=value,
                    parent_id=block_id,
                    variables_map=variables_map,
                    lists_map=lists_map,
                    param_scope=param_scope,
                    default_kind=default_kind,
                )
            },
            "fields": {},
            "shadow": False,
            "topLevel": False,
        }
        return block_id

    def _emit_no_input_stmt(self, blocks: dict[str, dict], opcode: str, parent_id: str) -> str:
        block_id = self._new_block_id()
        blocks[block_id] = {
            "opcode": opcode,
            "next": None,
            "parent": parent_id,
            "inputs": {},
            "fields": {},
            "shadow": False,
            "topLevel": False,
        }
        return block_id

    def _emit_repeat_stmt(
        self,
        blocks: dict[str, dict],
        stmt: RepeatStmt,
        parent_id: str,
        variables_map: dict[str, str],
        lists_map: dict[str, str],
        signatures: dict[str, _ProcedureSignature],
        param_scope: set[str],
    ) -> str:
        block_id = self._new_block_id()
        block = {
            "opcode": "control_repeat",
            "next": None,
            "parent": parent_id,
            "inputs": {
                "TIMES": self._expr_input(
                    blocks=blocks,
                    expr=stmt.times,
                    parent_id=block_id,
                    variables_map=variables_map,
                    lists_map=lists_map,
                    param_scope=param_scope,
                    default_kind="number",
                )
            },
            "fields": {},
            "shadow": False,
            "topLevel": False,
        }
        blocks[block_id] = block
        sub_first, _ = self._emit_statement_chain(
            blocks=blocks,
            statements=stmt.body,
            parent_id=block_id,
            variables_map=variables_map,
            lists_map=lists_map,
            signatures=signatures,
            param_scope=param_scope,
        )
        if sub_first is not None:
            block["inputs"]["SUBSTACK"] = [2, sub_first]
        return block_id

    def _emit_forever_stmt(
        self,
        blocks: dict[str, dict],
        stmt: ForeverStmt,
        parent_id: str,
        variables_map: dict[str, str],
        lists_map: dict[str, str],
        signatures: dict[str, _ProcedureSignature],
        param_scope: set[str],
    ) -> str:
        block_id = self._new_block_id()
        block = {
            "opcode": "control_forever",
            "next": None,
            "parent": parent_id,
            "inputs": {},
            "fields": {},
            "shadow": False,
            "topLevel": False,
        }
        blocks[block_id] = block
        sub_first, _ = self._emit_statement_chain(
            blocks=blocks,
            statements=stmt.body,
            parent_id=block_id,
            variables_map=variables_map,
            lists_map=lists_map,
            signatures=signatures,
            param_scope=param_scope,
        )
        if sub_first is not None:
            block["inputs"]["SUBSTACK"] = [2, sub_first]
        return block_id

    def _emit_stop_stmt(
        self,
        blocks: dict[str, dict],
        stmt: StopStmt,
        parent_id: str,
        variables_map: dict[str, str],
        lists_map: dict[str, str],
        param_scope: set[str],
    ) -> str:
        block_id = self._new_block_id()
        option_literal = self._literal_input(stmt.option)
        stop_option = "all"
        if option_literal is not None and option_literal[0] == 10:
            stop_option = str(option_literal[1])
        blocks[block_id] = {
            "opcode": "control_stop",
            "next": None,
            "parent": parent_id,
            "inputs": {},
            "fields": {"STOP_OPTION": [stop_option, None]},
            "shadow": False,
            "topLevel": False,
            "mutation": {"tagName": "mutation", "children": [], "hasnext": "false"},
        }
        return block_id

    def _emit_add_to_list_stmt(
        self,
        blocks: dict[str, dict],
        stmt: AddToListStmt,
        parent_id: str,
        variables_map: dict[str, str],
        lists_map: dict[str, str],
        param_scope: set[str],
    ) -> str:
        list_id = self._lookup_list_id(lists_map, stmt.list_name)
        block_id = self._new_block_id()
        blocks[block_id] = {
            "opcode": "data_addtolist",
            "next": None,
            "parent": parent_id,
            "inputs": {
                "ITEM": self._expr_input(
                    blocks=blocks,
                    expr=stmt.item,
                    parent_id=block_id,
                    variables_map=variables_map,
                    lists_map=lists_map,
                    param_scope=param_scope,
                    default_kind="string",
                )
            },
            "fields": {"LIST": [stmt.list_name, list_id]},
            "shadow": False,
            "topLevel": False,
        }
        return block_id

    def _emit_delete_of_list_stmt(
        self,
        blocks: dict[str, dict],
        stmt: DeleteOfListStmt,
        parent_id: str,
        variables_map: dict[str, str],
        lists_map: dict[str, str],
        param_scope: set[str],
    ) -> str:
        list_id = self._lookup_list_id(lists_map, stmt.list_name)
        block_id = self._new_block_id()
        blocks[block_id] = {
            "opcode": "data_deleteoflist",
            "next": None,
            "parent": parent_id,
            "inputs": {
                "INDEX": self._expr_input(
                    blocks=blocks,
                    expr=stmt.index,
                    parent_id=block_id,
                    variables_map=variables_map,
                    lists_map=lists_map,
                    param_scope=param_scope,
                    default_kind="number",
                )
            },
            "fields": {"LIST": [stmt.list_name, list_id]},
            "shadow": False,
            "topLevel": False,
        }
        return block_id

    def _emit_delete_all_of_list_stmt(
        self,
        blocks: dict[str, dict],
        stmt: DeleteAllOfListStmt,
        parent_id: str,
        lists_map: dict[str, str],
    ) -> str:
        list_id = self._lookup_list_id(lists_map, stmt.list_name)
        block_id = self._new_block_id()
        blocks[block_id] = {
            "opcode": "data_deletealloflist",
            "next": None,
            "parent": parent_id,
            "inputs": {},
            "fields": {"LIST": [stmt.list_name, list_id]},
            "shadow": False,
            "topLevel": False,
        }
        return block_id

    def _emit_insert_at_list_stmt(
        self,
        blocks: dict[str, dict],
        stmt: InsertAtListStmt,
        parent_id: str,
        variables_map: dict[str, str],
        lists_map: dict[str, str],
        param_scope: set[str],
    ) -> str:
        list_id = self._lookup_list_id(lists_map, stmt.list_name)
        block_id = self._new_block_id()
        blocks[block_id] = {
            "opcode": "data_insertatlist",
            "next": None,
            "parent": parent_id,
            "inputs": {},
            "fields": {"LIST": [stmt.list_name, list_id]},
            "shadow": False,
            "topLevel": False,
        }
        blocks[block_id]["inputs"]["ITEM"] = self._expr_input(
            blocks=blocks,
            expr=stmt.item,
            parent_id=block_id,
            variables_map=variables_map,
            lists_map=lists_map,
            param_scope=param_scope,
            default_kind="string",
        )
        blocks[block_id]["inputs"]["INDEX"] = self._expr_input(
            blocks=blocks,
            expr=stmt.index,
            parent_id=block_id,
            variables_map=variables_map,
            lists_map=lists_map,
            param_scope=param_scope,
            default_kind="number",
        )
        return block_id

    def _emit_replace_item_of_list_stmt(
        self,
        blocks: dict[str, dict],
        stmt: ReplaceItemOfListStmt,
        parent_id: str,
        variables_map: dict[str, str],
        lists_map: dict[str, str],
        param_scope: set[str],
    ) -> str:
        list_id = self._lookup_list_id(lists_map, stmt.list_name)
        block_id = self._new_block_id()
        blocks[block_id] = {
            "opcode": "data_replaceitemoflist",
            "next": None,
            "parent": parent_id,
            "inputs": {},
            "fields": {"LIST": [stmt.list_name, list_id]},
            "shadow": False,
            "topLevel": False,
        }
        blocks[block_id]["inputs"]["INDEX"] = self._expr_input(
            blocks=blocks,
            expr=stmt.index,
            parent_id=block_id,
            variables_map=variables_map,
            lists_map=lists_map,
            param_scope=param_scope,
            default_kind="number",
        )
        blocks[block_id]["inputs"]["ITEM"] = self._expr_input(
            blocks=blocks,
            expr=stmt.item,
            parent_id=block_id,
            variables_map=variables_map,
            lists_map=lists_map,
            param_scope=param_scope,
            default_kind="string",
        )
        return block_id

    def _emit_if_stmt(
        self,
        blocks: dict[str, dict],
        stmt: IfStmt,
        parent_id: str,
        variables_map: dict[str, str],
        lists_map: dict[str, str],
        signatures: dict[str, _ProcedureSignature],
        param_scope: set[str],
    ) -> str:
        block_id = self._new_block_id()
        block = {
            "opcode": "control_if_else",
            "next": None,
            "parent": parent_id,
            "inputs": {
                "CONDITION": self._expr_input(
                    blocks=blocks,
                    expr=stmt.condition,
                    parent_id=block_id,
                    variables_map=variables_map,
                    lists_map=lists_map,
                    param_scope=param_scope,
                    default_kind="boolean",
                )
            },
            "fields": {},
            "shadow": False,
            "topLevel": False,
        }
        blocks[block_id] = block
        then_first, _ = self._emit_statement_chain(
            blocks=blocks,
            statements=stmt.then_body,
            parent_id=block_id,
            variables_map=variables_map,
            lists_map=lists_map,
            signatures=signatures,
            param_scope=param_scope,
        )
        else_first, _ = self._emit_statement_chain(
            blocks=blocks,
            statements=stmt.else_body,
            parent_id=block_id,
            variables_map=variables_map,
            lists_map=lists_map,
            signatures=signatures,
            param_scope=param_scope,
        )
        if then_first is not None:
            block["inputs"]["SUBSTACK"] = [2, then_first]
        if else_first is not None:
            block["inputs"]["SUBSTACK2"] = [2, else_first]
        return block_id

    def _emit_call_stmt(
        self,
        blocks: dict[str, dict],
        stmt: ProcedureCallStmt,
        parent_id: str,
        variables_map: dict[str, str],
        lists_map: dict[str, str],
        signatures: dict[str, _ProcedureSignature],
        param_scope: set[str],
    ) -> str:
        signature = signatures.get(stmt.name.lower())
        if signature is None:
            raise CodegenError(f"Unknown procedure '{stmt.name}' during code generation.")
        block_id = self._new_block_id()
        inputs: dict[str, list] = {}
        for arg_id, arg_expr in zip(signature.arg_ids, stmt.args):
            inputs[arg_id] = self._expr_input(
                blocks=blocks,
                expr=arg_expr,
                parent_id=block_id,
                variables_map=variables_map,
                lists_map=lists_map,
                param_scope=param_scope,
                default_kind="string",
            )
        blocks[block_id] = {
            "opcode": "procedures_call",
            "next": None,
            "parent": parent_id,
            "inputs": inputs,
            "fields": {},
            "shadow": False,
            "topLevel": False,
            "mutation": {
                "tagName": "mutation",
                "children": [],
                "proccode": signature.proccode,
                "argumentids": json.dumps(signature.arg_ids),
                "warp": "false",
            },
        }
        return block_id

    def _expr_input(
        self,
        blocks: dict[str, dict],
        expr: Expr,
        parent_id: str,
        variables_map: dict[str, str],
        lists_map: dict[str, str],
        param_scope: set[str],
        default_kind: str,
    ) -> list:
        literal = self._literal_input(expr)
        if literal is not None:
            return [1, literal]
        reporter_id = self._emit_expr_reporter(
            blocks=blocks,
            expr=expr,
            parent_id=parent_id,
            variables_map=variables_map,
            lists_map=lists_map,
            param_scope=param_scope,
        )
        if reporter_id is None:
            return [1, self._default_shadow(default_kind)]
        return [2, reporter_id]

    def _emit_expr_reporter(
        self,
        blocks: dict[str, dict],
        expr: Expr,
        parent_id: str,
        variables_map: dict[str, str],
        lists_map: dict[str, str],
        param_scope: set[str],
    ) -> str | None:
        if isinstance(expr, NumberExpr) or isinstance(expr, StringExpr):
            return None
        if isinstance(expr, BuiltinReporterExpr):
            opcode_map = {
                "answer": "sensing_answer",
                "mouse_x": "sensing_mousex",
                "mouse_y": "sensing_mousey",
                "timer": "sensing_timer",
            }
            opcode = opcode_map.get(expr.kind)
            if opcode is None:
                raise CodegenError(f"Unsupported built-in reporter '{expr.kind}'.")
            block_id = self._new_block_id()
            blocks[block_id] = {
                "opcode": opcode,
                "next": None,
                "parent": parent_id,
                "inputs": {},
                "fields": {},
                "shadow": False,
                "topLevel": False,
            }
            return block_id
        if isinstance(expr, VarExpr):
            param_lookup = {name.lower() for name in param_scope}
            lowered = expr.name.lower()
            if lowered in param_lookup:
                block_id = self._new_block_id()
                blocks[block_id] = {
                    "opcode": "argument_reporter_string_number",
                    "next": None,
                    "parent": parent_id,
                    "inputs": {},
                    "fields": {"VALUE": [expr.name, None]},
                    "shadow": False,
                    "topLevel": False,
                }
                return block_id
            var_id = self._lookup_var_id(variables_map, expr.name)
            block_id = self._new_block_id()
            blocks[block_id] = {
                "opcode": "data_variable",
                "next": None,
                "parent": parent_id,
                "inputs": {},
                "fields": {"VARIABLE": [expr.name, var_id]},
                "shadow": False,
                "topLevel": False,
            }
            return block_id
        if isinstance(expr, PickRandomExpr):
            block_id = self._new_block_id()
            blocks[block_id] = {
                "opcode": "operator_random",
                "next": None,
                "parent": parent_id,
                "inputs": {},
                "fields": {},
                "shadow": False,
                "topLevel": False,
            }
            blocks[block_id]["inputs"]["FROM"] = self._expr_input(
                blocks=blocks,
                expr=expr.start,
                parent_id=block_id,
                variables_map=variables_map,
                lists_map=lists_map,
                param_scope=param_scope,
                default_kind="number",
            )
            blocks[block_id]["inputs"]["TO"] = self._expr_input(
                blocks=blocks,
                expr=expr.end,
                parent_id=block_id,
                variables_map=variables_map,
                lists_map=lists_map,
                param_scope=param_scope,
                default_kind="number",
            )
            return block_id
        if isinstance(expr, ListItemExpr):
            block_id = self._new_block_id()
            list_id = self._lookup_list_id(lists_map, expr.list_name)
            blocks[block_id] = {
                "opcode": "data_itemoflist",
                "next": None,
                "parent": parent_id,
                "inputs": {},
                "fields": {"LIST": [expr.list_name, list_id]},
                "shadow": False,
                "topLevel": False,
            }
            blocks[block_id]["inputs"]["INDEX"] = self._expr_input(
                blocks=blocks,
                expr=expr.index,
                parent_id=block_id,
                variables_map=variables_map,
                lists_map=lists_map,
                param_scope=param_scope,
                default_kind="number",
            )
            return block_id
        if isinstance(expr, ListLengthExpr):
            block_id = self._new_block_id()
            list_id = self._lookup_list_id(lists_map, expr.list_name)
            blocks[block_id] = {
                "opcode": "data_lengthoflist",
                "next": None,
                "parent": parent_id,
                "inputs": {},
                "fields": {"LIST": [expr.list_name, list_id]},
                "shadow": False,
                "topLevel": False,
            }
            return block_id
        if isinstance(expr, ListContainsExpr):
            block_id = self._new_block_id()
            list_id = self._lookup_list_id(lists_map, expr.list_name)
            blocks[block_id] = {
                "opcode": "data_listcontainsitem",
                "next": None,
                "parent": parent_id,
                "inputs": {},
                "fields": {"LIST": [expr.list_name, list_id]},
                "shadow": False,
                "topLevel": False,
            }
            blocks[block_id]["inputs"]["ITEM"] = self._expr_input(
                blocks=blocks,
                expr=expr.item,
                parent_id=block_id,
                variables_map=variables_map,
                lists_map=lists_map,
                param_scope=param_scope,
                default_kind="string",
            )
            return block_id
        if isinstance(expr, KeyPressedExpr):
            block_id = self._new_block_id()
            menu_id = self._new_block_id()
            blocks[block_id] = {
                "opcode": "sensing_keypressed",
                "next": None,
                "parent": parent_id,
                "inputs": {"KEY_OPTION": [1, menu_id]},
                "fields": {},
                "shadow": False,
                "topLevel": False,
            }
            key_literal = self._literal_input(expr.key)
            key_value = "space"
            if key_literal is not None and key_literal[0] == 10:
                key_value = str(key_literal[1])
            blocks[menu_id] = {
                "opcode": "sensing_keyoptions",
                "next": None,
                "parent": block_id,
                "inputs": {},
                "fields": {"KEY_OPTION": [key_value, None]},
                "shadow": True,
                "topLevel": False,
            }
            return block_id
        if isinstance(expr, UnaryExpr):
            if expr.op == "-":
                block_id = self._new_block_id()
                blocks[block_id] = {
                    "opcode": "operator_subtract",
                    "next": None,
                    "parent": parent_id,
                    "inputs": {},
                    "fields": {},
                    "shadow": False,
                    "topLevel": False,
                }
                blocks[block_id]["inputs"]["NUM1"] = [1, [4, "0"]]
                blocks[block_id]["inputs"]["NUM2"] = self._expr_input(
                    blocks=blocks,
                    expr=expr.operand,
                    parent_id=block_id,
                    variables_map=variables_map,
                    lists_map=lists_map,
                    param_scope=param_scope,
                    default_kind="number",
                )
                return block_id
            if expr.op == "not":
                block_id = self._new_block_id()
                blocks[block_id] = {
                    "opcode": "operator_not",
                    "next": None,
                    "parent": parent_id,
                    "inputs": {
                        "OPERAND": self._expr_input(
                            blocks=blocks,
                            expr=expr.operand,
                            parent_id=block_id,
                            variables_map=variables_map,
                            lists_map=lists_map,
                            param_scope=param_scope,
                            default_kind="boolean",
                        )
                    },
                    "fields": {},
                    "shadow": False,
                    "topLevel": False,
                }
                return block_id
            raise CodegenError(f"Unsupported unary operator '{expr.op}'.")
        if isinstance(expr, BinaryExpr):
            return self._emit_binary_expr(blocks, expr, parent_id, variables_map, lists_map, param_scope)
        raise CodegenError(f"Unsupported expression type '{type(expr).__name__}'.")

    def _emit_binary_expr(
        self,
        blocks: dict[str, dict],
        expr: BinaryExpr,
        parent_id: str,
        variables_map: dict[str, str],
        lists_map: dict[str, str],
        param_scope: set[str],
    ) -> str:
        if expr.op in {"<=", ">="}:
            op_first = "<" if expr.op == "<=" else ">"
            first = BinaryExpr(line=expr.line, column=expr.column, op=op_first, left=expr.left, right=expr.right)
            second = BinaryExpr(line=expr.line, column=expr.column, op="=", left=expr.left, right=expr.right)
            rewritten = BinaryExpr(line=expr.line, column=expr.column, op="or", left=first, right=second)
            return self._emit_binary_expr(blocks, rewritten, parent_id, variables_map, lists_map, param_scope)
        opcode_map = {
            "+": "operator_add",
            "-": "operator_subtract",
            "*": "operator_multiply",
            "/": "operator_divide",
            "%": "operator_mod",
            "<": "operator_lt",
            ">": "operator_gt",
            "=": "operator_equals",
            "==": "operator_equals",
            "and": "operator_and",
            "or": "operator_or",
        }
        if expr.op == "!=":
            equals_expr = BinaryExpr(line=expr.line, column=expr.column, op="=", left=expr.left, right=expr.right)
            not_expr = UnaryExpr(line=expr.line, column=expr.column, op="not", operand=equals_expr)
            reporter = self._emit_expr_reporter(blocks, not_expr, parent_id, variables_map, lists_map, param_scope)
            if reporter is None:
                raise CodegenError("Failed to emit inequality expression.")
            return reporter
        opcode = opcode_map.get(expr.op)
        if opcode is None:
            raise CodegenError(f"Unsupported binary operator '{expr.op}'.")
        block_id = self._new_block_id()
        blocks[block_id] = {
            "opcode": opcode,
            "next": None,
            "parent": parent_id,
            "inputs": {},
            "fields": {},
            "shadow": False,
            "topLevel": False,
        }
        input_map = {
            "operator_add": ("NUM1", "NUM2", "number"),
            "operator_subtract": ("NUM1", "NUM2", "number"),
            "operator_multiply": ("NUM1", "NUM2", "number"),
            "operator_divide": ("NUM1", "NUM2", "number"),
            "operator_mod": ("NUM1", "NUM2", "number"),
            "operator_lt": ("OPERAND1", "OPERAND2", "number"),
            "operator_gt": ("OPERAND1", "OPERAND2", "number"),
            "operator_equals": ("OPERAND1", "OPERAND2", "string"),
            "operator_and": ("OPERAND1", "OPERAND2", "boolean"),
            "operator_or": ("OPERAND1", "OPERAND2", "boolean"),
        }
        left_key, right_key, kind = input_map[opcode]
        blocks[block_id]["inputs"][left_key] = self._expr_input(
            blocks=blocks,
            expr=expr.left,
            parent_id=block_id,
            variables_map=variables_map,
            lists_map=lists_map,
            param_scope=param_scope,
            default_kind=kind,
        )
        blocks[block_id]["inputs"][right_key] = self._expr_input(
            blocks=blocks,
            expr=expr.right,
            parent_id=block_id,
            variables_map=variables_map,
            lists_map=lists_map,
            param_scope=param_scope,
            default_kind=kind,
        )
        return block_id

    def _literal_input(self, expr: Expr) -> list | None:
        if isinstance(expr, NumberExpr):
            value = int(expr.value) if expr.value.is_integer() else expr.value
            return [4, str(value)]
        if isinstance(expr, StringExpr):
            return [10, expr.value]
        return None

    def _default_shadow(self, kind: str) -> list:
        if kind == "number":
            return [4, "0"]
        return [10, ""]

    def _lookup_var_id(self, variables_map: dict[str, str], var_name: str) -> str:
        var_id = variables_map.get(var_name.lower())
        if var_id is None:
            raise CodegenError(f"Variable '{var_name}' is not declared.")
        return var_id

    def _lookup_list_id(self, lists_map: dict[str, str], list_name: str) -> str:
        list_id = lists_map.get(list_name.lower())
        if list_id is None:
            raise CodegenError(f"List '{list_name}' is not declared.")
        return list_id

    def _build_costumes(self, target: Target) -> list[dict]:
        costumes = list(target.costumes)
        if not costumes:
            if target.is_stage:
                costumes.append(CostumeDecl(line=target.line, column=target.column, path="__default_stage_backdrop__.svg"))
            else:
                costumes.append(CostumeDecl(line=target.line, column=target.column, path="__default_sprite_costume__.svg"))

        costume_json: list[dict] = []
        for idx, costume in enumerate(costumes, start=1):
            rotation_center_x = 0.0
            rotation_center_y = 0.0
            if costume.path == "__default_stage_backdrop__.svg":
                data = DEFAULT_STAGE_SVG
                ext = "svg"
                name = f"backdrop{idx}"
            elif costume.path == "__default_sprite_costume__.svg":
                data = DEFAULT_SPRITE_SVG
                ext = "svg"
                name = f"costume{idx}"
            else:
                file_path = Path(costume.path)
                if not file_path.is_absolute():
                    candidates = [
                        self.source_dir / file_path,
                        self.source_dir.parent / file_path,
                        Path.cwd() / file_path,
                    ]
                    file_path = next((candidate for candidate in candidates if candidate.exists()), candidates[0])
                if not file_path.exists() or not file_path.is_file():
                    raise CodegenError(
                        f"Costume file not found for target '{target.name}': '{costume.path}' resolved to '{file_path}'."
                    )
                ext = file_path.suffix.lower().lstrip(".")
                if ext not in {"svg", "png"}:
                    raise CodegenError(
                        f"Unsupported costume format '{file_path.suffix}' for '{file_path}'. Only .svg and .png are supported."
                    )
                data = file_path.read_bytes()
                name = file_path.stem

            if ext == "svg":
                data, rotation_center_x, rotation_center_y = self._prepare_svg(data=data, source_name=costume.path)

            digest = hashlib.md5(data).hexdigest()
            md5ext = f"{digest}.{ext}"
            self.assets[md5ext] = data
            entry = {
                "name": name,
                "assetId": digest,
                "md5ext": md5ext,
                "dataFormat": ext,
                "rotationCenterX": rotation_center_x,
                "rotationCenterY": rotation_center_y,
            }
            if ext == "png":
                entry["bitmapResolution"] = 1
            costume_json.append(entry)
        return costume_json

    def _prepare_svg(self, data: bytes, source_name: str) -> tuple[bytes, float, float]:
        try:
            root = ET.fromstring(data)
        except ET.ParseError as exc:
            raise CodegenError(f"Invalid SVG file '{source_name}': {exc}.") from exc

        min_x, min_y, width, height = self._read_svg_bounds(root, source_name)
        if self.scale_svgs:
            root = self._normalize_svg_root(root, min_x, min_y, width, height, DEFAULT_SVG_TARGET_SIZE)
            centered = DEFAULT_SVG_TARGET_SIZE / 2.0
            return ET.tostring(root, encoding="utf-8"), centered, centered
        return ET.tostring(root, encoding="utf-8"), width / 2.0, height / 2.0

    def _normalize_svg_root(
        self,
        root: ET.Element,
        min_x: float,
        min_y: float,
        width: float,
        height: float,
        target_size: float,
    ) -> ET.Element:
        scale_x = target_size / width
        scale_y = target_size / height
        transform = (
            f"translate({self._fmt(-min_x)} {self._fmt(-min_y)}) "
            f"scale({self._fmt(scale_x)} {self._fmt(scale_y)})"
        )
        group_tag = self._svg_tag(root, "g")
        wrapper = ET.Element(group_tag, {"transform": transform})
        children = list(root)
        for child in children:
            root.remove(child)
            wrapper.append(child)
        root.set("viewBox", f"0 0 {self._fmt(target_size)} {self._fmt(target_size)}")
        root.set("width", self._fmt(target_size))
        root.set("height", self._fmt(target_size))
        root.append(wrapper)
        return root

    def _read_svg_bounds(self, root: ET.Element, source_name: str) -> tuple[float, float, float, float]:
        view_box = root.get("viewBox")
        if view_box:
            parsed = self._parse_view_box(view_box, source_name)
            if parsed is not None:
                return parsed

        width = self._parse_svg_length(root.get("width"))
        height = self._parse_svg_length(root.get("height"))
        if width is not None and height is not None and width > 0 and height > 0:
            return 0.0, 0.0, width, height
        return 0.0, 0.0, DEFAULT_SVG_TARGET_SIZE, DEFAULT_SVG_TARGET_SIZE

    def _parse_view_box(self, view_box: str, source_name: str) -> tuple[float, float, float, float] | None:
        parts = [piece for piece in re.split(r"[\s,]+", view_box.strip()) if piece]
        if len(parts) != 4:
            return None
        try:
            min_x, min_y, width, height = (float(piece) for piece in parts)
        except ValueError as exc:
            raise CodegenError(f"Invalid SVG viewBox in '{source_name}': '{view_box}'.") from exc
        if width <= 0 or height <= 0:
            raise CodegenError(f"SVG viewBox must have positive width/height in '{source_name}'.")
        return min_x, min_y, width, height

    def _parse_svg_length(self, value: str | None) -> float | None:
        if value is None:
            return None
        match = re.match(r"^\s*([+-]?(?:\d+(?:\.\d*)?|\.\d+))", value)
        if not match:
            return None
        number = float(match.group(1))
        if number <= 0:
            return None
        return number

    def _svg_tag(self, root: ET.Element, name: str) -> str:
        if root.tag.startswith("{") and "}" in root.tag:
            namespace = root.tag[1 : root.tag.index("}")]
            return f"{{{namespace}}}{name}"
        return name

    def _fmt(self, value: float) -> str:
        if float(value).is_integer():
            return str(int(value))
        return f"{value:.6f}".rstrip("0").rstrip(".")

    def _collect_broadcast_ids(self) -> dict[str, str]:
        messages: set[str] = set()
        for target in self.project.targets:
            for script in target.scripts:
                if script.event_type == "when_i_receive" and script.message:
                    messages.add(script.message)
                self._collect_messages_from_statements(script.body, messages)
            for procedure in target.procedures:
                self._collect_messages_from_statements(procedure.body, messages)
        return {message: self._new_id("broadcast") for message in sorted(messages)}

    def _collect_messages_from_statements(self, statements: list[Statement], messages: set[str]) -> None:
        for stmt in statements:
            if isinstance(stmt, BroadcastStmt):
                messages.add(stmt.message)
            elif isinstance(stmt, RepeatStmt):
                self._collect_messages_from_statements(stmt.body, messages)
            elif isinstance(stmt, IfStmt):
                self._collect_messages_from_statements(stmt.then_body, messages)
                self._collect_messages_from_statements(stmt.else_body, messages)

    def _broadcast_id(self, message: str) -> str:
        broadcast_id = self.broadcast_ids.get(message)
        if broadcast_id is None:
            broadcast_id = self._new_id("broadcast")
            self.broadcast_ids[message] = broadcast_id
        return broadcast_id

    def _new_id(self, prefix: str) -> str:
        self._id_counter += 1
        return f"{prefix}_{self._id_counter}"

    def _new_block_id(self) -> str:
        return self._new_id("block")
