import glob

from afm.gui.help_text import LIMIT, split_help


def test_short_text_is_unchanged():
    assert split_help("Hola. Chau.") == ("Hola. Chau.", "")


def test_cuts_at_sentence_and_loses_nothing():
    text = ("Primera oración corta. " * 5 + "Segunda parte muy larga que sigue. " * 20).strip()
    short, rest = split_help(text)
    assert 0 < len(short) <= LIMIT and short.endswith(".") and rest
    assert " ".join((short + " " + rest).split()) == " ".join(text.split())


def test_one_giant_sentence_is_cut_at_a_space():
    text = "palabra " * 100
    short, rest = split_help(text)
    assert len(short) <= LIMIT and short.endswith("…") and rest and "palabra" in rest


def test_every_real_help_block_gets_a_short_part():
    import ast
    for f in glob.glob("src/afm/gui/steps/*.py"):
        for n in ast.walk(ast.parse(open(f, encoding="utf-8").read())):
            if isinstance(n, ast.Call) and getattr(n.func, "id", getattr(n.func, "attr", "")) in ("_help", "help_note") and len(n.args) >= 2:
                try:
                    txt = ast.literal_eval(n.args[1])
                except Exception:
                    continue
                if isinstance(txt, str):
                    assert len(split_help(txt)[0]) <= LIMIT, (f, n.lineno)
