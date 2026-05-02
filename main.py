# main.py — MFS IDE для Android (Kivy)
# Запуск: python main.py (для теста на ПК)
# Сборка APK: buildozer android debug

import os
import sys
import threading
from io import StringIO

from kivy.app import App
from kivy.uix.boxlayout import BoxLayout
from kivy.uix.gridlayout import GridLayout
from kivy.uix.scrollview import ScrollView
from kivy.uix.textinput import TextInput
from kivy.uix.label import Label
from kivy.uix.button import Button
from kivy.uix.tabbedpanel import TabbedPanel, TabbedPanelItem
from kivy.uix.popup import Popup
from kivy.core.window import Window
from kivy.utils import get_color_from_hex
from kivy.clock import Clock, mainthread
from kivy.metrics import dp, sp

# ─── цвета ──────────────────────────────────────────────
BG       = get_color_from_hex('#0a0c10')
SURFACE  = get_color_from_hex('#0f1318')
BORDER   = get_color_from_hex('#1e2a32')
ACCENT   = get_color_from_hex('#00e5ccff')
GREEN    = get_color_from_hex('#2ecc71ff')
RED      = get_color_from_hex('#e74c3cff')
YELLOW   = get_color_from_hex('#f39c12ff')
TEXT     = get_color_from_hex('#dce8e8ff')
TEXT2    = get_color_from_hex('#7a9090ff')
TEXT3    = get_color_from_hex('#3d5555ff')
ACCENT2  = get_color_from_hex('#ff6b35ff')

Window.clearcolor = BG

# ════════════════════════════════════════════════
#  ЛЕКСЕР
# ════════════════════════════════════════════════
import re

MFS_KEYWORDS = {
    'ifel','cls','d','msg','msgln','get','conv','cyc',
    'err','mass','all','math','str','lib','libout',
    'goaway','zr','back','slp','fire','inv','clout','inh'
}

TOKEN_REGEX = [
    ('STRING',  r'"[^"]*"'),
    ('NUMBER',  r'\d+(\.\d+)?'),
    ('OP',      r'inv[=<>!]|==|<=|>=|[+\-*/=<>]'),
    ('PUNCT',   r'[(){}\[\];,./]'),
    ('BOOL',    r'\b(T|F)\b'),
    ('WORD',    r'[A-Za-z_]\w*'),
    ('SPACE',   r'[ \t\r\n]+'),
]
_COMPILED = [(t, re.compile(rx)) for t, rx in TOKEN_REGEX]
_COMMA_NUM = re.compile(r'\d+,\d+')


class MFSError(Exception):
    def __init__(self, msg, line=None):
        super().__init__(msg)
        self.mfs_line = line


def check_comma_numbers(code):
    in_string = False
    for i, ch in enumerate(code):
        if ch == '"':
            in_string = not in_string
        if not in_string:
            m = _COMMA_NUM.match(code, i)
            if m:
                raise MFSError(
                    "Дробные числа — только через точку!\nПример: 3.14  (не 3,14)"
                )


def lex(code):
    check_comma_numbers(code)
    tokens = []
    pos = 0
    line = 1
    while pos < len(code):
        matched = False
        for token_type, pattern in _COMPILED:
            m = pattern.match(code, pos)
            if m:
                val = m.group(0)
                if token_type == 'SPACE':
                    line += val.count('\n')
                else:
                    if token_type == 'WORD':
                        low = val.lower()
                        t = 'KEYWORD' if low in MFS_KEYWORDS else 'WORD'
                        v = low if low in MFS_KEYWORDS else val
                        tokens.append((t, v, line))
                    else:
                        tokens.append((token_type, m.group(0), line))
                pos = m.end()
                matched = True
                break
        if not matched:
            raise MFSError(f"Неизвестный символ '{code[pos]}'", line)
    return tokens


# ════════════════════════════════════════════════
#  ИНТЕРПРЕТАТОР
# ════════════════════════════════════════════════
INV_MAP = {'inv=': '!=', 'inv>': '<=', 'inv<': '>=', 'inv!': '=='}


class MFSRuntime:
    def __init__(self, print_fn, input_fn):
        self.v       = {}        # переменные
        self.cls     = {}        # классы
        self.print_  = print_fn  # (text, style) -> None
        self.input_  = input_fn  # () -> str  (блокирующий)
        self.stopped = False

    def tv(self, tt, tv):
        if tt == 'STRING': return tv[1:-1]
        if tt == 'NUMBER': return float(tv) if '.' in tv else int(tv)
        if tt == 'BOOL':   return tv == 'T'
        if tv == 'zr':     return None
        if tv in self.v:   return self.v[tv]
        return tv

    def until(self, toks, s, ends):
        r, i = [], s
        while i < len(toks):
            if toks[i][1] in ends: return r, i + 1
            r.append(toks[i]); i += 1
        return r, i

    def parens(self, toks, s):
        d, inn, i = 0, [], s
        while i < len(toks):
            v = toks[i][1]
            if v == '(':
                d += 1
                if d > 1: inn.append(toks[i])
            elif v == ')':
                d -= 1
                if d == 0: return inn, i + 1
                inn.append(toks[i])
            else:
                inn.append(toks[i])
            i += 1
        raise MFSError("Нет закрывающей ')'")

    def block(self, toks, s):
        if s >= len(toks) or toks[s][1] != '{':
            raise MFSError("Ожидалась '{'", toks[s][2] if s < len(toks) else None)
        d, inn, i = 0, [], s
        while i < len(toks):
            v = toks[i][1]
            if v == '{':
                d += 1
                if d > 1: inn.append(toks[i])
            elif v == '}':
                d -= 1
                if d == 0: return inn, i + 1
                inn.append(toks[i])
            else:
                inn.append(toks[i])
            i += 1
        raise MFSError("Нет закрывающей '}'")

    def split_comma(self, toks):
        parts, cur = [], []
        for t in toks:
            if t[1] == ',': parts.append(cur); cur = []
            else: cur.append(t)
        parts.append(cur)
        return parts

    def expr(self, e):
        if not e: return None
        if len(e) == 1: return self.tv(e[0][0], e[0][1])
        parts = []
        for tt, tv, *_ in e:
            if tt == 'OP' and tv in INV_MAP:
                parts.append(INV_MAP[tv])
            elif tt == 'BOOL':
                parts.append('True' if tv == 'T' else 'False')
            elif tv == 'zr':
                parts.append('None')
            elif tt == 'STRING':
                parts.append(repr(tv[1:-1]))
            elif tt in ('WORD', 'KEYWORD') and tv not in ('T', 'F', 'zr'):
                if tv in self.v:
                    val = self.v[tv]
                    parts.append(repr(val) if isinstance(val, str) else str(val))
                else:
                    parts.append(repr(tv))
            else:
                parts.append(tv)
        es = ' '.join(parts)
        try:
            return eval(es, {"__builtins__": {}}, {})
        except Exception as ex:
            raise MFSError(f"Ошибка выражения [{es}]: {ex}", e[0][2] if e else None)

    def is_get_msg(self, e):
        return (len(e) == 4 and e[0][1] == 'get' and e[1][1] == 'msg'
                and e[2][1] == '(' and e[3][1] == ')')

    def expr_or_get(self, e):
        if self.is_get_msg(e):
            raw = self.input_()
            try: return int(raw)
            except ValueError:
                try: return float(raw)
                except ValueError: return raw
        return self.expr(e)

    def run(self, toks):
        i = 0
        while i < len(toks) and not self.stopped:
            tt, tv = toks[i][0], toks[i][1]
            ln = toks[i][2] if len(toks[i]) > 2 else 0

            if tt == 'KEYWORD':

                if tv == 'msgln':
                    e, i = self.until(toks, i+1, {';'})
                    r = self.expr_or_get(e)
                    self.print_('' if r is None else str(r), 'out')

                elif tv == 'msg':
                    e, i = self.until(toks, i+1, {';'})
                    r = self.expr_or_get(e)
                    self.print_('' if r is None else str(r), 'inline')

                elif tv == 'clout':
                    self.print_(None, 'clear'); i += 1

                elif tv in ('math', 'str', 'all'):
                    vt = tv; vn = toks[i+1][1]
                    s = i + 2
                    if s < len(toks) and toks[s][1] == '=': s += 1
                    e, i = self.until(toks, s, {';'})
                    val = self.expr_or_get(e)
                    if vt == 'str':
                        val = '' if val is None else str(val)
                    elif vt == 'math':
                        try: val = float(val) if isinstance(val, str) and '.' in val else (int(val) if isinstance(val, str) else val)
                        except: val = 0
                    self.v[vn] = val

                elif tv == 'fire':
                    vn = toks[i+1][1]; self.v.pop(vn, None)
                    _, i = self.until(toks, i+1, {';'})

                elif tv == 'goaway':
                    self.stopped = True; return '__stop__'

                elif tv == 'slp':
                    j = i + 1
                    if j < len(toks) and toks[j][1] == '(':
                        inn, j = self.parens(toks, j)
                        if j < len(toks) and toks[j][1] == ';': j += 1
                        import time
                        secs = self.expr(inn)
                        try: time.sleep(float(secs))
                        except: pass
                    i = j

                elif tv == 'err':
                    j = i + 1
                    while j < len(toks) and toks[j][1] != '{': j += 1
                    body, j = self.block(toks, j)
                    try: self.run(body)
                    except MFSError as ex:
                        self.print_(f"err: {ex}", 'warn')
                    except Exception as ex:
                        self.print_(f"err: {ex}", 'warn')
                    i = j

                elif tv == 'conv':
                    j = i+1; ct = vn = None
                    if j < len(toks) and toks[j][1] == '<':
                        j++; ct = toks[j][1]; j += 1
                        if j < len(toks) and toks[j][1] == '>': j += 1
                    if j < len(toks) and toks[j][1] == '(':
                        inn, j = self.parens(toks, j)
                        if inn: vn = inn[0][1]
                    if j < len(toks) and toks[j][1] == ';': j += 1
                    if vn and ct and vn in self.v:
                        val = self.v[vn]
                        if ct == 'math': self.v[vn] = float(val) if '.' in str(val) else int(float(val))
                        elif ct == 'str': self.v[vn] = str(val)
                        elif ct == 'bool': self.v[vn] = bool(val)
                    i = j

                elif tv == 'mass':
                    an = toks[i+1][1]; j = i + 2
                    while j < len(toks) and toks[j][1] != '[': j += 1
                    j += 1
                    items, cur = [], []
                    while j < len(toks) and toks[j][1] != ']':
                        if toks[j][1] == ',':
                            if cur: items.append(self.expr(cur[:])); cur.clear()
                        else: cur.append(toks[j])
                        j += 1
                    if cur: items.append(self.expr(cur))
                    self.v[an] = items; j += 1
                    if j < len(toks) and toks[j][1] == ';': j += 1
                    i = j

                elif tv == 'get': i += 1

                elif tv == 'ifel':
                    j = i + 1
                    while j < len(toks) and toks[j][1] != '(': j += 1
                    ci, j = self.parens(toks, j)
                    cond = self.expr(ci)
                    while j < len(toks) and toks[j][1] != '{': j += 1
                    tb, j = self.block(toks, j)
                    eb = []
                    if j < len(toks) and toks[j][1] == '/':
                        j += 1
                        while j < len(toks) and toks[j][1] != '{': j += 1
                        eb, j = self.block(toks, j)
                    i = j
                    if cond:
                        r = self.run(tb)
                        if r == '__stop__': return '__stop__'
                    elif eb:
                        r = self.run(eb)
                        if r == '__stop__': return '__stop__'

                elif tv == 'cyc':
                    j = i + 1; ct = toks[j][1]; j += 1
                    while j < len(toks) and toks[j][1] != '(': j += 1
                    ai, j = self.parens(toks, j)
                    while j < len(toks) and toks[j][1] != '{': j += 1
                    bb, j = self.block(toks, j); i = j

                    if ct == 'f':
                        p = self.split_comma(ai)
                        sv = int(self.expr(p[0])) if p else 0
                        ev = int(self.expr(p[1])) if len(p) > 1 else 0
                        st = int(self.expr(p[2])) if len(p) > 2 else 1
                        for _ in range(sv, ev, st):
                            if self.run(bb) == '__stop__': return '__stop__'
                    elif ct == 'wh':
                        cnt = 0
                        while self.expr(ai) and not self.stopped and cnt < 1_000_000:
                            if self.run(bb) == '__stop__': return '__stop__'
                            cnt += 1
                    elif ct == 'fr':
                        p = self.split_comma(ai)
                        itn = p[0][0][1] if p else ''
                        an  = p[1][0][1] if len(p) > 1 else ''
                        arr = self.v.get(an, [])
                        if isinstance(arr, list):
                            for el in arr:
                                if self.stopped: return '__stop__'
                                self.v[itn] = el
                                if self.run(bb) == '__stop__': return '__stop__'

                elif tv == 'back':
                    e, i = self.until(toks, i+1, {';'})
                    return self.expr(e)

                elif tv == 'cls':
                    cn = toks[i+1][1]; j = i + 2
                    while j < len(toks) and toks[j][1] != '{': j += 1
                    body, j = self.block(toks, j); i = j
                    saved = dict(self.v)
                    self.run(body)
                    self.cls[cn] = {k: v for k, v in self.v.items() if k not in saved}

                elif tv == 'd':
                    j = i + 1
                    if j < len(toks) and toks[j][1] == 'cls': j += 1
                    pn = toks[j][1]; j += 1
                    while j < len(toks) and toks[j][1] != '{': j += 1
                    body, j = self.block(toks, j); i = j
                    # inh
                    bi = 0
                    while bi < len(body):
                        if body[bi][0] == 'KEYWORD' and body[bi][1] == 'inh':
                            bj = bi + 1
                            while bj < len(body) and body[bj][1] != '{': bj += 1
                            inh_block, bnj = self.block(body, bj)
                            names = [t[1] for t in inh_block if t[1] not in (',', ';')]
                            pv = self.cls.get(pn, {})
                            for name in names:
                                if name in pv: self.v[name] = pv[name]
                                else: self.print_(f"Предупреждение: '{name}' не найдено в '{pn}'", 'warn')
                            bi = bnj
                            if bi < len(body) and body[bi][1] == ';': bi += 1
                        else: bi += 1
                    self.run(body)

                elif tv == 'inh':
                    self.print_("inh используется только внутри d cls", 'warn')
                    j = i + 1
                    while j < len(toks) and toks[j][1] != '{': j += 1
                    _, j = self.block(toks, j)
                    if j < len(toks) and toks[j][1] == ';': j += 1
                    i = j

                elif tv in ('lib', 'libout'):
                    name = toks[i+1][1] if i+1 < len(toks) else '?'
                    path = f"{name}.mfslib"
                    if os.path.exists(path):
                        with open(path, 'r', encoding='utf-8') as f:
                            sub = lex(f.read())
                        self.run(sub)
                    else:
                        self.print_(f"Lib '{name}' не найдена", 'warn')
                    i += 2

                else: i += 1

            elif tt == 'WORD' and tv in self.v:
                if i+1 < len(toks) and toks[i+1][1] == '=':
                    e, i = self.until(toks, i+2, {';'})
                    self.v[tv] = self.expr_or_get(e)
                else: i += 1
            else: i += 1

        return None


# ════════════════════════════════════════════════
#  KIVY UI
# ════════════════════════════════════════════════

class MFSApp(App):
    def build(self):
        self.title = 'MFS IDE'
        self.inline_buf = ''
        self.input_event = threading.Event()
        self.input_value = ''
        self.running = False

        # Главный layout
        root = BoxLayout(orientation='vertical')

        # ── Шапка ──────────────────────────────
        header = BoxLayout(
            size_hint_y=None, height=dp(48),
            padding=[dp(8), dp(6)], spacing=dp(8)
        )
        header.canvas.before.add(
            __import__('kivy.graphics', fromlist=['Color']).Color(*SURFACE)
        )

        logo = Label(
            text='[color=00e5cc][b]MFS[/b][/color][color=ff6b35].[/color]IDE',
            markup=True, size_hint_x=None, width=dp(100),
            font_size=sp(17), halign='left', valign='middle'
        )
        logo.bind(size=logo.setter('text_size'))

        self.run_btn = Button(
            text='▶ RUN', size_hint_x=None, width=dp(90),
            background_color=GREEN, color=(0,0,0,1),
            font_size=sp(13), bold=True
        )
        self.run_btn.bind(on_press=lambda _: self.run_code())

        clr_btn = Button(
            text='Очистить', size_hint_x=None, width=dp(90),
            background_color=SURFACE, color=TEXT2,
            font_size=sp(12)
        )
        clr_btn.bind(on_press=lambda _: self.clear_output())

        header.add_widget(logo)
        header.add_widget(clr_btn)
        header.add_widget(self.run_btn)
        root.add_widget(header)

        # ── TabbedPanel ────────────────────────
        tabs = TabbedPanel(do_default_tab=False)
        tabs.tab_width = dp(110)

        # TAB: Редактор
        tab_editor = TabbedPanelItem(text='Редактор')
        editor_layout = BoxLayout(orientation='vertical')

        self.editor = TextInput(
            text=self._example_hello(),
            font_name='RobotoMono-Regular' if os.path.exists('RobotoMono-Regular.ttf') else 'Roboto',
            font_size=sp(13),
            background_color=BG,
            foreground_color=TEXT,
            cursor_color=ACCENT,
            multiline=True,
            auto_indent=True,
        )
        editor_layout.add_widget(self.editor)
        tab_editor.content = editor_layout

        # TAB: Терминал
        tab_term = TabbedPanelItem(text='Терминал')
        term_layout = BoxLayout(orientation='vertical')

        # статус
        self.status_lbl = Label(
            text='[color=3d5555]● готов[/color]',
            markup=True,
            size_hint_y=None, height=dp(28),
            font_size=sp(11), halign='left', padding_x=dp(10)
        )
        self.status_lbl.bind(size=self.status_lbl.setter('text_size'))
        term_layout.add_widget(self.status_lbl)

        # вывод
        scroll = ScrollView()
        self.output_lbl = Label(
            text='', markup=True,
            size_hint_y=None, font_size=sp(13),
            halign='left', valign='top',
            padding=(dp(10), dp(6)),
            color=TEXT
        )
        self.output_lbl.bind(texture_size=lambda inst, val: setattr(inst, 'height', val[1]))
        self.output_lbl.bind(width=lambda inst, val: setattr(inst, 'text_size', (val, None)))
        scroll.add_widget(self.output_lbl)
        self._output_scroll = scroll
        term_layout.add_widget(scroll)

        # ввод
        input_row = BoxLayout(size_hint_y=None, height=dp(44), spacing=dp(6), padding=[dp(8),dp(4)])
        prompt = Label(text='[color=2ecc71]›[/color]', markup=True, size_hint_x=None, width=dp(20))
        self.stdin = TextInput(
            hint_text='ввод пользователя...',
            multiline=False, font_size=sp(13),
            background_color=SURFACE, foreground_color=ACCENT2,
            disabled=True
        )
        self.stdin.bind(on_text_validate=self._on_enter)
        send_btn = Button(
            text='Enter', size_hint_x=None, width=dp(70),
            background_color=SURFACE, color=ACCENT, font_size=sp(12)
        )
        send_btn.bind(on_press=lambda _: self._on_enter(self.stdin))
        input_row.add_widget(prompt)
        input_row.add_widget(self.stdin)
        input_row.add_widget(send_btn)
        term_layout.add_widget(input_row)
        tab_term.content = term_layout

        # TAB: Примеры
        tab_ex = TabbedPanelItem(text='Примеры')
        ex_layout = ScrollView()
        ex_grid = GridLayout(cols=1, spacing=dp(8), padding=dp(10), size_hint_y=None)
        ex_grid.bind(minimum_height=ex_grid.setter('height'))

        examples = [
            ('👋 Привет мир',          self._example_hello),
            ('🔢 Калькулятор',         self._example_calc),
            ('🎮 Угадай число',        self._example_game),
            ('📋 Массивы',             self._example_arrays),
            ('🏗️ Классы и inh',       self._example_classes),
        ]
        for name, fn in examples:
            btn = Button(
                text=name, size_hint_y=None, height=dp(52),
                background_color=SURFACE, color=TEXT, font_size=sp(14)
            )
            btn.bind(on_press=lambda _, f=fn: self._load_example(f))
            ex_grid.add_widget(btn)

        ex_layout.add_widget(ex_grid)
        tab_ex.content = ex_layout

        tabs.add_widget(tab_editor)
        tabs.add_widget(tab_term)
        tabs.add_widget(tab_ex)
        tabs.default_tab = tab_editor

        root.add_widget(tabs)
        return root

    # ── Загрузка примера ──────────────────────
    def _load_example(self, fn):
        self.editor.text = fn()

    # ── Очистить вывод ──────────────────────
    def clear_output(self):
        self.output_lbl.text = ''
        self.inline_buf = ''

    # ── Обновление статуса ──────────────────
    @mainthread
    def set_status(self, color, text):
        self.status_lbl.text = f'[color={color}]● {text}[/color]'

    # ── Добавить вывод ──────────────────────
    @mainthread
    def append_out(self, text, style):
        if style == 'clear':
            self.output_lbl.text = ''; self.inline_buf = ''; return
        if style == 'inline':
            self.inline_buf += text
            lines = self.output_lbl.text.rsplit('\n', 1)
            if len(lines) > 1:
                self.output_lbl.text = lines[0] + '\n' + self.inline_buf
            else:
                self.output_lbl.text = self.inline_buf
            return
        if style == 'out': self.inline_buf = ''
        if style == 'out':
            chunk = f'[color=dce8e8]{text}[/color]\n'
        elif style == 'err':
            chunk = f'[color=e74c3c]⚠ {text}[/color]\n'
        elif style == 'warn':
            chunk = f'[color=f39c12]⚡ {text}[/color]\n'
        elif style == 'sys':
            chunk = f'[color=3d5555]{text}[/color]\n'
        elif style == 'inp':
            chunk = f'[color=ff6b35]› {text}[/color]\n'
        else:
            chunk = text + '\n'
        self.output_lbl.text += chunk
        Clock.schedule_once(lambda _: setattr(self._output_scroll, 'scroll_y', 0), .05)

    # ── Ввод пользователя ───────────────────
    @mainthread
    def _enable_input(self):
        self.stdin.disabled = False
        self.stdin.focus = True

    def _on_enter(self, instance):
        if not self.input_event.is_set():
            self.input_value = instance.text
            instance.text = ''
            self.append_out(self.input_value, 'inp')
            instance.disabled = True
            self.input_event.set()

    def _blocking_input(self):
        self.input_event.clear()
        self._enable_input()
        self.input_event.wait()      # блокируем поток интерпретатора
        return self.input_value

    # ── Запуск кода ─────────────────────────
    def run_code(self):
        if self.running:
            return
        self.running = True
        self.clear_output()
        self.set_status('f39c12', 'выполняется...')
        self.run_btn.disabled = True
        threading.Thread(target=self._run_thread, daemon=True).start()

    def _run_thread(self):
        code = self.editor.text
        try:
            toks = lex(code)
        except MFSError as e:
            line_info = f"Строка {e.mfs_line}: " if e.mfs_line else ''
            self.append_out(f"{line_info}{e}", 'err')
            self.set_status('e74c3c', 'ошибка лексера')
            self.run_btn.disabled = False
            self.running = False
            return

        rt = MFSRuntime(
            print_fn=self.append_out,
            input_fn=self._blocking_input
        )
        try:
            rt.run(toks)
            if not rt.stopped:
                self.append_out('─── программа завершена ───', 'sys')
                self.set_status('00e5cc', 'готово')
            else:
                self.set_status('00e5cc', 'завершено (goaway)')
        except MFSError as e:
            line_info = f"Строка {e.mfs_line}: " if e.mfs_line else ''
            self.append_out(f"{line_info}{e}", 'err')
            self.set_status('e74c3c', 'ошибка')
        except Exception as e:
            self.append_out(f"Критическая ошибка: {e}", 'err')
            self.set_status('e74c3c', 'ошибка')
        finally:
            Clock.schedule_once(lambda _: setattr(self.run_btn, 'disabled', False))
            self.running = False

    # ════ ПРИМЕРЫ ════════════════════════════

    def _example_hello(self):
        return '''msgln "Привет, MFS!";

math x = 10;
math y = 32;
math sum = x + y;
msg "Сумма: ";
msgln sum;

ifel (sum > 40) {
    msgln "Больше сорока!";
} / {
    msgln "Не больше сорока.";
}
goaway;'''

    def _example_calc(self):
        return '''msgln "=== Калькулятор ===";
msgln "Введи первое число:";
math a = get msg();
msgln "Введи второе число:";
math b = get msg();
msgln "Операция (+, -, *, /):";
str op = get msg();

ifel (op == "+") {
    msg "Результат: "; msgln a + b;
} / {
    ifel (op == "-") {
        msg "Результат: "; msgln a - b;
    } / {
        ifel (op == "*") {
            msg "Результат: "; msgln a * b;
        } / {
            ifel (b inv= 0) {
                msg "Результат: "; msgln a / b;
            } / {
                msgln "Деление на ноль!";
            }
        }
    }
}
goaway;'''

    def _example_game(self):
        return '''msgln "=== Угадай число (1-100) ===";
math secret = 42;
math tries  = 0;
math won    = 0;
str  hint   = "";

cyc wh(tries < 7) {
    msg "Попытка "; msg tries + 1; msgln ":";
    math guess = get msg();
    tries = tries + 1;
    ifel (guess == secret) {
        won = 1; tries = 8;
    } / {
        ifel (guess < secret) {
            hint = "Выше!";
        } / {
            hint = "Ниже!";
        }
        msgln hint;
    }
}

ifel (won == 1) {
    msgln "Угадал! Молодец!";
} / {
    msg "Было загадано: "; msgln secret;
}
goaway;'''

    def _example_arrays(self):
        return '''mass nums [10, 20, 30, 40, 50];
mass names ["Аня", "Боря", "Вася"];

msgln "Числа:";
all n = 0;
cyc fr(n, nums) {
    msg "  "; msgln n;
}

msgln "Имена:";
all name = "";
cyc fr(name, names) {
    msg "  Привет, "; msgln name;
}
goaway;'''

    def _example_classes(self):
        return '''cls Animal {
    str name  = "Животное";
    math speed = 10;
    msg "Создан: "; msgln name;
}

d cls Animal {
    inh { name, speed };
    str breed = "Лабрадор";
    math tricks = 5;
}

msg "Имя: ";    msgln name;
msg "Порода: "; msgln breed;
msg "Скорость: "; msgln speed;
goaway;'''


if __name__ == '__main__':
    MFSApp().run()
