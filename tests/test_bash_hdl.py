from mutator.cli import run
from mutator.sites import discover_raw


def replacements(source, language):
    return {(site.original, site.mutant, site.line) for site in discover_raw(source, language, "")}


def test_bash_contextual_operators():
    source = """run() {
  [[ $1 -gt 0 && $1 != '!=' ]] || return 1
  ((x = 1 + y))
  [[ a < b ]]
  [[ ! -z $1 ]]
  echo '-gt' 1
  >out
  cat <<'EOF'
0 1 && -gt
EOF
}
"""
    assert replacements(source, "bash") == {
        ("-gt", "-ge", 2), ("0", "1", 2), ("&&", "||", 2), ("!=", "==", 2),
        ("||", "&&", 2), ("1", "0", 2), ("1", "0", 3), ("+", "-", 3),
        ("!", "", 5), ("-z", "-n", 5),
    }


def test_hdl_contextual_operators():
    source = """module m;
always @(posedge clk) begin
  if (a <= b && c !== 1'b0) q <= '1;
  else q <= !c;
  q[0] <= 4'h1;
end
if (1) begin : gen
  assign q = 1;
end
endmodule
"""
    assert replacements(source, "systemverilog") == {
        ("<=", "<", 3), ("&&", "||", 3), ("!==", "===", 3),
        ("1'b0", "1'b1", 3), ("'1", "'0", 3), ("!", "", 4),
        ("0", "1", 5), ("4'h1", "4'h0", 5), ("1", "0", 8),
    }


def test_bash_cli_runs_test_script_for_each_mutant(tmp_path, capsys):
    source = tmp_path / "program"
    original = "#!/bin/bash\nstatus() { return 0; }\n"
    source.write_text(original)
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests/run.sh").write_text('source ./program\nstatus\n')
    assert run(["--root", str(tmp_path), "--no-coverage", "--max-workers", "2"]) == 0
    assert "KILLED" in capsys.readouterr().out
    assert source.read_text() == original


def test_bash_uses_cobertura_and_an_explicit_command(tmp_path, capsys):
    (tmp_path / "exit.sh").write_text("status() {\n  return 0\n}\nstatus\n")
    report = tmp_path / "coverage/bash/cobertura.xml"
    report.parent.mkdir(parents=True)
    report.write_text(
        '<coverage><packages><package><classes><class filename="exit.sh">'
        '<lines><line number="2" hits="1"/></lines>'
        "</class></classes></package></packages></coverage>"
    )
    assert run([
        "--root", str(tmp_path), "--use-existing-coverage", "--test-command", "bash exit.sh",
    ]) == 0
    assert "KILLED" in capsys.readouterr().out


def test_hdl_cli_recompiles_in_the_worker(tmp_path, capsys):
    import shutil

    import pytest

    if not shutil.which("iverilog") or not shutil.which("vvp"):
        pytest.skip("Для проверки мутаций RTL нужен Icarus Verilog.")
    original = "module m(output q);\n  assign q = 1'b1;\nendmodule\n"
    source = tmp_path / "m.v"
    source.write_text(original)
    (tmp_path / "m_tb.sv").write_text(
        "module m_tb;\n"
        "  wire q;\n"
        "  m dut(q);\n"
        "  initial begin\n"
        "    #1;\n"
        "    if (q !== 1'b1) $fatal(1, \"unexpected output\");\n"
        "    $finish;\n"
        "  end\n"
        "endmodule\n"
    )
    (tmp_path / "Makefile").write_text(
        "test:\n\tmkdir -p build\n"
        "\tiverilog -g2012 -s m_tb -o build/check m.v m_tb.sv\n"
        "\tvvp build/check\n"
    )
    assert run(["--root", str(tmp_path), "--no-coverage", "--max-workers", "2"]) == 0
    assert "KILLED    m.v:2 1'b1 -> 1'b0" in capsys.readouterr().out
    assert source.read_text() == original


def test_bash_arithmetic_and_test_operators_stay_in_their_contexts():
    source = """status() {
  true && false
  ((t = x > y))
  [[ $1 = thing ]]
  [[ $1 != other ]]
  [[ ! (x < y) ]]
  exit 0
}
"""
    assert replacements(source, "bash") == {
        ("true", "false", 2), ("&&", "||", 2), ("false", "true", 2),
        (">", ">=", 3), ("=", "!=", 4), ("!=", "==", 5), ("!", "", 6), ("0", "1", 7),
    }
