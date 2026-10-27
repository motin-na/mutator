from pathlib import Path
import shutil

import pytest

from mutator.functions import sites_in_file
from mutator.coverage import covered_lines
from mutator.engine import mutate_file
from mutator.runner import CommandRunner
from mutator.runner import test_plan as plan
from mutator.sites import discover_raw
from mutator.workers import create_workers, delete_tree


@pytest.mark.parametrize('language, suffix', [('c', '.c'), ('cpp', '.cpp')])
def test_mutation_sites_are_bound_to_c_cpp_functions(tmp_path, language, suffix):
    source = 'int choose(int x) { return x > 0 ? 1 : x + 1; }\n'
    path = tmp_path / ('source' + suffix)
    path.write_text(source)
    sites = sites_in_file(source, path, tmp_path, path.name)
    assert sites
    assert {site.name for site in sites} == {'choose'}
    assert ('>', '>=') in {(site.original, site.mutant) for site in sites}
    assert ('0', '1') in {(site.original, site.mutant) for site in sites}


def test_cpp_literals_comments_preprocessor_and_types_are_not_mutated():
    source = '''#define LIMIT 1
#if LIMIT > 0
bool choose(int n) {
    const char *s = R"tag(1 + true > 0)tag";
    // 0 + 1
    int values[1];
    static_assert(1 == 1);
    return n > 0 and true;
}
#endif
'''
    sites = discover_raw(source, 'cpp', 'source.cpp')
    assert {site.line for site in sites} == {8}
    assert {(site.original, site.mutant) for site in sites} == {('>', '>='), ('0', '1'), ('and', 'or'), ('true', 'false')}


def test_cpp_pointer_declarators_are_not_arithmetic_sites():
    source = 'int *choose(int *p, int &x) { return x > 0 ? p : nullptr; }'
    sites = discover_raw(source, 'cpp', 'source.cpp')
    assert '*' not in {site.original for site in sites}


def test_cpp_alternative_equality_and_integer_suffixes():
    source = 'bool f(int x) { return x not_eq 0U && x < 1LL; }'
    pairs = {(site.original, site.mutant) for site in discover_raw(source, 'cpp', 'a.cpp')}
    assert {('not_eq', '=='), ('0U', '1U'), ('1LL', '0LL')} <= pairs


def test_old_crapper_is_reported_instead_of_silently_skipping_cpp(monkeypatch):
    from mutator.cli import _require_crapper
    from mutator.crapper_link import ensure_crapper

    crapper = ensure_crapper()
    monkeypatch.setattr(crapper.discover, 'language_of', lambda path: None)
    message = _require_crapper()
    assert message and 'C/C++' in message and 'motin-na/crapper' in message


def test_cmake_plan_rebuilds_then_runs_ctest(tmp_path):
    (tmp_path / 'CMakeLists.txt').write_text('project(demo)')
    command, cwd = plan(tmp_path, tmp_path / 'source.cpp', 'cpp', None)
    assert cwd == tmp_path
    assert 'cmake' in str(command) and 'ctest' in str(command)


def test_c_cpp_workers_copy_build_inputs_and_headers(tmp_path):
    (tmp_path / 'src').mkdir()
    (tmp_path / 'include').mkdir()
    (tmp_path / 'tests').mkdir()
    (tmp_path / 'CMakeLists.txt').write_text('project(demo)')
    header = tmp_path / 'include/value.hpp'
    original = b'inline int value() { return 1; }\n'
    header.write_bytes(original)
    (tmp_path / 'src/main.cpp').write_text('#include "../include/value.hpp"\n')
    (tmp_path / 'tests/test.cpp').write_text('#include "../include/value.hpp"\n')
    (tmp_path / 'build').mkdir()
    (tmp_path / 'build/CMakeCache.txt').write_text('Старый кэш')
    base = tmp_path / 'target/mutation-workers/run-test'
    workers = create_workers(base, tmp_path, 'include/value.hpp', original, 2)
    for worker in workers:
        assert not (worker / 'src/main.cpp').is_symlink()
        assert not (worker / 'tests/test.cpp').is_symlink()
        assert not (worker / 'CMakeLists.txt').is_symlink()
        assert not (worker / 'build').exists()
        (worker / 'include/value.hpp').write_text('Мутация')
    assert header.read_bytes() == original
    delete_tree(base)


@pytest.mark.skipif(not all(shutil.which(x) for x in ['gcc', 'g++', 'gcov', 'cmake', 'ctest']), reason='Нужны GCC и CMake')
@pytest.mark.parametrize('suffix', ['.c', '.cpp', '.hpp'])
def test_real_compiled_mutants_are_killed_survive_or_stay_uncovered(tmp_path, suffix):
    from mutator.crapper_link import ensure_crapper

    root = tmp_path / 'проект с пробелом'
    root.mkdir()
    (root / 'include').mkdir()
    (root / 'tests').mkdir()
    path = root / ('include/value' + suffix)
    prefix = 'inline ' if suffix == '.hpp' else ''
    source = ''.join(prefix + definition for definition in [
        'int value(int x) {\n    return x + 1;\n}\n',
        'int positive(int x) {\n    return x > 0;\n}\n',
        'int unused(int x) {\n    return x + 1;\n}\n',
    ])
    path.write_text(source)
    if suffix == '.hpp':
        declarations = '#include "../include/value.hpp"\n'
        sources = ''
    else:
        declarations = 'int value(int);\nint positive(int);\n'
        if suffix == '.c':
            declarations = 'extern "C" {\n' + declarations + '}\n'
        sources = 'include/value' + suffix
    (root / 'tests/main.cpp').write_text(declarations + 'int main() { return value(2) == 3 && positive(2) ? 0 : 1; }\n')
    (root / 'CMakeLists.txt').write_text('''cmake_minimum_required(VERSION 3.16)
project(sample LANGUAGES C CXX)
enable_testing()
add_executable(sample tests/main.cpp ''' + sources + ''')
add_test(NAME sample COMMAND sample)
''')
    crapper = ensure_crapper()
    crapper.runners.run_coverage(root, [path], None)
    language = crapper.discover.language_of(path)
    lines = covered_lines(root, path, language)
    assert lines
    result = mutate_file(
        path, root, runner=CommandRunner(), covered_lines=lines,
        ignore_coverage=False, mutate_all=True, lines=None, test_command=None,
        timeout_factor=20, mutation_warning=50, baselines={}, max_workers=2,
    )
    assert not result.baseline_failed, result.baseline_message
    by_name = {form.name: form for form in result.forms}
    assert by_name['value'].killed == 2
    assert by_name['positive'].survived == 2
    assert by_name['unused'].uncovered == 2
    assert path.read_text() == source
    assert result.written
    assert not list((root / 'target/mutation-workers').glob('run-*'))
