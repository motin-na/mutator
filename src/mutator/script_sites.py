"""Мутации выражений и команд Bash, Verilog и SystemVerilog."""

import re

_BASH_TEST = {
    "-eq": "-ne", "-ne": "-eq",
    "-lt": "-le", "-le": "-lt", "-gt": "-ge", "-ge": "-gt",
    "-z": "-n", "-n": "-z", "=": "!=", "==": "!=", "!=": "==",
}
_BITWISE = {"&": "|", "|": "&", "^": "&", "<<": ">>", ">>": "<<"}
_STATUS_COMMANDS = {b"exit", b"return"}
_BOOLEAN_COMMANDS = {"true": "false", "false": "true"}
_HDL_NON_RUNTIME = {
    "constant_expression", "data_type", "delay_control", "event_control",
    "attribute_instance", "string_literal", "text_macro_usage",
}
_HDL_INTEGER = re.compile(r"(?:(?:[0-9][0-9_]*)?\s*'[sS]?[bBoOdDhH]\s*)?0*[01]")


def _text(node):
    return node.text.decode("utf-8")


def _constant(value):
    return value[:-1] + ("1" if value.endswith("0") else "0")


def _bash_command(node):
    name = node.child_by_field_name("name")
    arguments = node.children_by_field_name("argument")
    if name is None:
        return
    text = _text(name)
    if text in _BOOLEAN_COMMANDS:
        yield name, text, _BOOLEAN_COMMANDS[text], "boolean"
    if name.text in _STATUS_COMMANDS and len(arguments) == 1:
        value = _text(arguments[0])
        if value in {"0", "1"}:
            yield arguments[0], value, _constant(value), "constant"


def _test_expression(node):
    current = node.parent
    while current is not None and current.type in {"binary_expression", "unary_expression", "parenthesized_expression"}:
        current = current.parent
    return current is not None and current.type == "test_command"


def _bash_operator(node, value, binary):
    if value.startswith("-") and value in _BASH_TEST:
        return _BASH_TEST[value]
    if value == "=" and _test_expression(node):
        return "!="
    if value in {"<", ">"} and _test_expression(node):
        return None
    return _BITWISE.get(value) or binary.get(value)


def _bash_binary(node, binary, category):
    op = node.child_by_field_name("operator")
    if op is None:
        return
    value = _text(op)
    changed = _bash_operator(node, value, binary)
    if changed:
        yield op, value, changed, _BASH_CATEGORY.get(value, category(value))


_BASH_CATEGORY = {
    "-eq": "equality", "-ne": "equality", "=": "equality",
    "-lt": "comparison", "-le": "comparison", "-gt": "comparison", "-ge": "comparison",
}


def _bash_unary(node):
    op = node.child_by_field_name("operator")
    if op is None:
        return
    value = _text(op)
    changed = "" if value == "!" else _BASH_TEST.get(value)
    if changed is not None:
        yield op, value, changed, "unary"


def _bash_number(node):
    value = _text(node)
    if value in {"0", "1"} and node.parent.type in {
        "binary_expression", "unary_expression", "ternary_expression",
        "arithmetic_expansion", "variable_assignment",
    }:
        yield node, value, _constant(value), "constant"


def _bash_list(node):
    for child in node.children:
        if child.type in {"&&", "||"}:
            yield child, child.type, "||" if child.type == "&&" else "&&", "logical"


def _bash_negated(node):
    yield node.children[0], "!", "", "unary"


_BASH_NODES = {
    "command": _bash_command,
    "unary_expression": _bash_unary,
    "negated_command": _bash_negated,
    "list": _bash_list,
    "number": _bash_number,
}


def _bash_node(node, binary, category):
    if node.type == "binary_expression":
        yield from _bash_binary(node, binary, category)
    visitor = _BASH_NODES.get(node.type)
    if visitor is not None:
        yield from visitor(node)


def _hdl_expression(node, binary, category):
    op = node.child_by_field_name("operator")
    if op is None:
        return
    value = _text(op)
    if value == "!" and op.type == "unary_operator":
        yield op, value, "", "unary"
        return
    if node.child_by_field_name("left") is not None:
        replacement = _BITWISE.get(value) or binary.get(value)
        if replacement is not None:
            yield op, value, replacement, category(value)


def _hdl_integer(node):
    value = _text(node)
    if value in {"'0", "'1"} or _HDL_INTEGER.fullmatch(value):
        yield node, value, _constant(value), "constant"


def _hdl_node(node, binary, category):
    if node.type == "expression":
        yield from _hdl_expression(node, binary, category)
    if node.type in {"integral_number", "unbased_unsized_literal"}:
        yield from _hdl_integer(node)


def script_mutations(root, language, binary, category):
    visit = _bash_node if language == "bash" else _hdl_node
    skip = set() if language == "bash" else _HDL_NON_RUNTIME
    stack = [root]
    while stack:
        node = stack.pop()
        if node.type in skip or node.is_error:
            continue
        yield from visit(node, binary, category)
        stack.extend(reversed(node.named_children))
