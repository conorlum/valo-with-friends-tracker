"""Import-boundary inspection, including relative and literal dynamic imports."""
import ast
import importlib.util


def imports(path, module):
    names = set()
    package = module.rpartition('.')[0]
    for node in ast.walk(ast.parse(path.read_text(encoding='utf-8'))):
        if isinstance(node, ast.Import): names.update(a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom):
            base = node.module or ''
            if node.level: base = importlib.util.resolve_name('.'*node.level + base, package)
            names.add(base)
            names.update(f'{base}.{a.name}' for a in node.names)
        elif isinstance(node, ast.Call) and node.args and isinstance(node.args[0], ast.Constant):
            function = ast.unparse(node.func)
            value = node.args[0].value
            if function in ('__import__', 'importlib.import_module') and isinstance(value, str):
                names.add(importlib.util.resolve_name(value, package) if value.startswith('.') else value)
    return names
