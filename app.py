import streamlit as st
import time
from datetime import datetime
from streamlit_autorefresh import st_autorefresh

st.set_page_config(page_title="PLC Neumático - Embotelladora", page_icon="🍾", layout="wide")

# ============================================================
# ESTADO INICIAL
# ============================================================
def init_state():
    d = {
        "DI_EMERGENCIA_OK": True,
        "DI_AUTO": True,
        "DI_BOTELLA": False,
        "DI_CIL_ARRIBA": True,
        "DI_CIL_ABAJO": False,
        "DI_PRESOSTATO": True,
        "DO_VALVULA_BAJAR": False,
        "DO_CINTA": False,
        "DO_PILOTO_AUTO": False,
        "DO_PILOTO_FALLO": False,
        "DO_PILOTO_CIL_ABAJO": False,
        "M_CICLO_ACTIVO": False,
        "M_FALLO": False,
        "C_CONTADOR": 0,
        "paro_pending": False,
        "cil_pos": 0.0,
        "bottle_x": -1.0,
        "bottle_state": "none",
        "T1_start": None, "T2_start": None,
        "T1_elapsed": 0.0, "T2_elapsed": 0.0,
        "state": "IDLE",
        "log": [],
        "last_tick": time.time(),
        "auto_bottle": True,
        "last_bottle_time": time.time() - 10,
    }
    for k, v in d.items():
        if k not in st.session_state:
            st.session_state[k] = v

init_state()

def log_event(msg):
    ts = datetime.now().strftime("%H:%M:%S.%f")[:-3]
    st.session_state.log.insert(0, f"`{ts}` {msg}")
    st.session_state.log = st.session_state.log[:40]

# ============================================================
# TICK: FÍSICA + LÓGICA PLC
# ============================================================
def tick(marcha_clk, paro_clk, reset_clk, bottle_clk, manual_down):
    now = time.time()
    dt = min(0.5, max(0.001, now - st.session_state.last_tick))
    st.session_state.last_tick = now

    # 1) Física cilindro (válvula monoestable: ON baja / OFF sube por muelle)
    speed = 200.0  # %/s
    if st.session_state.DO_VALVULA_BAJAR:
        st.session_state.cil_pos = min(100.0, st.session_state.cil_pos + speed * dt)
    else:
        st.session_state.cil_pos = max(0.0, st.session_state.cil_pos - speed * dt)
    st.session_state.DI_CIL_ABAJO = st.session_state.cil_pos >= 95.0
    st.session_state.DI_CIL_ARRIBA = st.session_state.cil_pos <= 5.0

    # 2) Timers
    if st.session_state.T1_start is not None:
        st.session_state.T1_elapsed = now - st.session_state.T1_start
    if st.session_state.T2_start is not None:
        st.session_state.T2_elapsed = now - st.session_state.T2_start

    # 3) Botella manual
    if bottle_clk and st.session_state.bottle_state == "none":
        st.session_state.bottle_state = "incoming"
        st.session_state.bottle_x = 0.0
        log_event("🍾 Botella en cinta de entrada")

    # 4) Emergencia / Reset
    if not st.session_state.DI_EMERGENCIA_OK:
        if not st.session_state.M_FALLO:
            log_event("🚨 PARO DE EMERGENCIA activado")
        st.session_state.M_FALLO = True
    if st.session_state.M_FALLO and reset_clk and st.session_state.DI_EMERGENCIA_OK:
        st.session_state.M_FALLO = False
        st.session_state.M_CICLO_ACTIVO = False
        st.session_state.state = "IDLE"
        log_event("✅ Fallo reseteado")

    # 5) Lógica PLC
    if st.session_state.M_FALLO:
        st.session_state.state = "FALLO"
        st.session_state.DO_VALVULA_BAJAR = False
        st.session_state.DO_CINTA = False
        st.session_state.DO_PILOTO_FALLO = True
        st.session_state.DO_PILOTO_AUTO = False
        st.session_state.M_CICLO_ACTIVO = False

    elif not st.session_state.DI_AUTO:
        st.session_state.state = "MANUAL"
        st.session_state.DO_PILOTO_FALLO = False
        st.session_state.DO_PILOTO_AUTO = False
        st.session_state.DO_CINTA = False
        st.session_state.DO_VALVULA_BAJAR = manual_down and st.session_state.DI_PRESOSTATO

    else:
        st.session_state.DO_PILOTO_FALLO = False
        st.session_state.DO_PILOTO_AUTO = True

        if not st.session_state.M_CICLO_ACTIVO:
            st.session_state.DO_VALVULA_BAJAR = False
            st.session_state.DO_CINTA = False

        if marcha_clk and not st.session_state.M_CICLO_ACTIVO:
            st.session_state.M_CICLO_ACTIVO = True
            st.session_state.paro_pending = False
            st.session_state.state = "WAIT_BOTTLE"
            log_event("▶ Marcha - Ciclo iniciado")

        if paro_clk:
            st.session_state.paro_pending = True
            log_event("⏸ Paro solicitado (fin de ciclo)")

        if st.session_state.M_CICLO_ACTIVO:
            s = st.session_state.state
            if s in ("IDLE", "WAIT_BOTTLE"):
                st.session_state.state = "WAIT_BOTTLE"
                st.session_state.DO_CINTA = True
                st.session_state.DO_VALVULA_BAJAR = False
                if st.session_state.DI_BOTELLA:
                    st.session_state.state = "STABILIZE"
                    st.session_state.T1_start = now
                    st.session_state.T1_elapsed = 0.0
                    log_event("⏳ Botella en estación, estabilizando")
            elif s == "STABILIZE":
                st.session_state.DO_CINTA = False
                if st.session_state.T1_elapsed >= 0.3:
                    st.session_state.state = "CYL_DOWN"
                    log_event("⬇ Bajando cilindro")
            elif s == "CYL_DOWN":
                st.session_state.DO_VALVULA_BAJAR = True
                if st.session_state.DI_CIL_ABAJO:
                    st.session_state.state = "PRESS"
                    st.session_state.T2_start = now
                    st.session_state.T2_elapsed = 0.0
                    log_event("🔨 Cilindro abajo, prensando tapa")
            elif s == "PRESS":
                st.session_state.DO_VALVULA_BAJAR = True
                if st.session_state.T2_elapsed >= 0.5:
                    st.session_state.state = "CYL_UP"
                    st.session_state.DO_VALVULA_BAJAR = False
                    log_event("⬆ Retornando cilindro")
            elif s == "CYL_UP":
                st.session_state.DO_VALVULA_BAJAR = False
                if st.session_state.DI_CIL_ARRIBA:
                    st.session_state.C_CONTADOR += 1
                    st.session_state.bottle_state = "leaving"
                    log_event(f"🍾 Botella tapada #{st.session_state.C_CONTADOR}")
                    if st.session_state.paro_pending:
                        st.session_state.M_CICLO_ACTIVO = False
                        st.session_state.paro_pending = False
                        st.session_state.state = "IDLE"
                        log_event("⏹ Ciclo detenido por Paro")
                    else:
                        st.session_state.state = "WAIT_BOTTLE"

    # 6) Simulación botella
    bs = st.session_state.bottle_state
    if bs == "incoming":
        st.session_state.DI_BOTELLA = False
        if st.session_state.DO_CINTA:
            st.session_state.bottle_x += 100 * dt
            if st.session_state.bottle_x >= 100:
                st.session_state.bottle_x = 100
                st.session_state.bottle_state = "at_station"
                st.session_state.DI_BOTELLA = True
    elif bs == "leaving":
        st.session_state.DI_BOTELLA = False
        if st.session_state.DO_CINTA:
            st.session_state.bottle_x += 150 * dt
            if st.session_state.bottle_x >= 220:
                st.session_state.bottle_state = "none"
                st.session_state.bottle_x = -1.0

    # 7) Pilotos
    st.session_state.DO_PILOTO_CIL_ABAJO = st.session_state.DI_CIL_ABAJO

    # 8) Auto-botellas
    if (st.session_state.auto_bottle and st.session_state.DI_AUTO
            and st.session_state.M_CICLO_ACTIVO and not st.session_state.M_FALLO
            and st.session_state.bottle_state == "none"
            and now - st.session_state.last_bottle_time > 0.5):
        st.session_state.bottle_state = "incoming"
        st.session_state.bottle_x = 0.0
        st.session_state.last_bottle_time = now

# ============================================================
# RENDER
# ============================================================
st_autorefresh(interval=100, limit=None, key="refresh")

st.title("🍾 Simulador PLC Neumático — Estación de Tapado")
st.caption("Cilindro de doble efecto + válvula 5/2 monoestable + sensores Reed")

# ---------- SIDEBAR ----------
with st.sidebar:
    st.header("🎛 Panel de Control")

    modo = st.radio(
        "Modo de operación", ["Automático", "Manual"],
        index=0 if st.session_state.DI_AUTO else 1,
        horizontal=True,
        disabled=st.session_state.M_CICLO_ACTIVO,
    )
    st.session_state.DI_AUTO = (modo == "Automático")

    st.divider()

    marcha_clk = paro_clk = False
    manual_down = False

    if st.session_state.DI_AUTO:
        st.subheader("Marcha / Paro")
        c1, c2 = st.columns(2)
        with c1:
            marcha_clk = st.button("▶ Marcha", use_container_width=True,
                                   type="primary", disabled=st.session_state.M_FALLO)
        with c2:
            paro_clk = st.button("⏸ Paro", use_container_width=True,
                                 disabled=st.session_state.M_FALLO)
    else:
        st.subheader("Control manual del cilindro")
        manual_down = st.checkbox("⬇ Extender cilindro (bajar)",
                                  key="manual_jog", disabled=st.session_state.M_FALLO)
        st.caption("Al soltar retorna por muelle")

    st.divider()

    st.subheader("Botellas")
    bottle_clk = st.button("🍾 Simular llegada de botella",
                            use_container_width=True,
                            disabled=(st.session_state.bottle_state != "none"))
    st.session_state.auto_bottle = st.checkbox(
        "Generar botellas automáticamente",
        value=st.session_state.auto_bottle,
    )

    st.divider()

    st.subheader("🔐 Seguridad")
    emerg = st.toggle("🚨 Paro de emergencia", key="emerg_toggle")
    st.session_state.DI_EMERGENCIA_OK = not emerg

    reset_clk = st.button("🔄 Reset Fallo", use_container_width=True,
                           disabled=(not st.session_state.M_FALLO
                                     or not st.session_state.DI_EMERGENCIA_OK))

    st.divider()

    if st.button("⚙ Forzar cambio presostato", use_container_width=True):
        st.session_state.DI_PRESOSTATO = not st.session_state.DI_PRESOSTATO
    st.caption(("🟢 Presión de aire OK" if st.session_state.DI_PRESOSTATO
                else "🔴 Sin presión de aire"))

# ---------- EJECUTAR TICK ----------
tick(marcha_clk, paro_clk, reset_clk, bottle_clk, manual_down)

# ---------- LAYOUT PRINCIPAL ----------
col_a, col_b, col_c = st.columns([1.3, 1.3, 1])

# ---- Columna A: Cilindro + Cinta ----
with col_a:
    st.subheader("🛠 Cilindro y cinta")

    pos = st.session_state.cil_pos
    arriba = st.session_state.DI_CIL_ARRIBA
    abajo = st.session_state.DI_CIL_ABAJO
    valvula = st.session_state.DO_VALVULA_BAJAR

    color_fill = "#4a90e2" if valvula else "#7aa7d8"
    st.markdown(f"""
    <div style="display:flex; gap:24px; align-items:center;">
      <div>
        <div style="width:110px; height:250px; border:3px solid #333;
                    border-radius:8px; position:relative; overflow:hidden;
                    background:#f4f4f4;">
          <div style="position:absolute; bottom:0; left:0; right:0;
                      height:{pos:.1f}%; background:{color_fill};
                      transition:height 0.08s linear;"></div>
          <div style="position:absolute; top:10px; right:-10px; width:18px;
                      height:18px; border-radius:50%;
                      background:{'#00e676' if arriba else '#444'};
                      box-shadow:0 0 6px {'#00e676' if arriba else 'transparent'};"></div>
          <div style="position:absolute; bottom:10px; right:-10px; width:18px;
                      height:18px; border-radius:50%;
                      background:{'#00e676' if abajo else '#444'};
                      box-shadow:0 0 6px {'#00e676' if abajo else 'transparent'};"></div>
        </div>
        <div style="text-align:center; margin-top:6px; font-size:12px; color:#666;">
          Pos: {pos:.0f}%
        </div>
      </div>
      <div style="font-size:14px; line-height:2;">
        <div><b>Electroválvula DO00</b>: {'🟢 ON (bajando)' if valvula else '⚫ OFF (retorno)'}</div>
        <div><b>Sensor arriba</b>: {'🟢 activo' if arriba else '⚫ inactivo'}</div>
        <div><b>Sensor abajo</b>: {'🟢 activo' if abajo else '⚫ inactivo'}</div>
        <div><b>Motor cinta DO01</b>: {'🟢 ON' if st.session_state.DO_CINTA else '⚫ OFF'}</div>
        <div><b>Presostato</b>: {'🟢 OK' if st.session_state.DI_PRESOSTATO else '🔴 FALLO'}</div>
      </div>
    </div>
    """, unsafe_allow_html=True)

    # Cinta + botella
    bx = st.session_state.bottle_x
    if bx < 0:
        bx_vis = -1
    else:
        bx_vis = min(bx, 200)
    left_pct = max(0, min(100, bx_vis / 2)) if bx_vis >= 0 else -20
    show_bottle = bx_vis >= 0 and bx_vis < 210
    bottle_html = (f'<div style="position:absolute; left:{left_pct}%; top:-40px; '
                   f'width:22px; height:40px; background:#bde0fe; '
                   f'border:2px solid #4895ef; border-radius:6px 6px 3px 3px; '
                   f'transition:left 0.08s linear;"></div>') if show_bottle else ""
    st.markdown(f"""
    <div style="position:relative; height:90px; margin-top:24px;">
      <div style="position:absolute; top:20px; left:0; right:0; height:14px;
                  background:#555; border-radius:3px;"></div>
      {bottle_html}
      <div style="position:absolute; top:34px; left:48%; width:6px; height:24px;
                  background:#d4a017; border-radius:3px;"></div>
      <div style="position:absolute; top:58px; left:42%; font-size:11px; color:#666;">
        Estación de tapado
      </div>
    </div>
    """, unsafe_allow_html=True)

# ---- Columna B: Estado PLC ----
with col_b:
    st.subheader("📊 Estado del PLC")

    estado_labels = {
        "IDLE": "⏹ Reposo",
        "WAIT_BOTTLE": "⏳ Esperando botella",
        "STABILIZE": "🕒 Estabilizando",
        "CYL_DOWN": "⬇ Bajando cilindro",
        "PRESS": "🔨 Prensando",
        "CYL_UP": "⬆ Subiendo cilindro",
        "MANUAL": "🎮 Manual",
        "FALLO": "🚨 FALLO",
    }
    st.markdown(f"### {estado_labels.get(st.session_state.state, st.session_state.state)}")

    # Diagrama de estados
    chain = ["WAIT_BOTTLE", "STABILIZE", "CYL_DOWN", "PRESS", "CYL_UP"]
    badges = []
    for s in chain:
        lbl = estado_labels[s].split(" ", 1)[1]
        if s == st.session_state.state:
            badges.append(f"**:orange[{lbl}]**")
        else:
            badges.append(f"<span style='color:#888'>{lbl}</span>")
    st.markdown(" → ".join(badges), unsafe_allow_html=True)

    st.divider()

    c1, c2 = st.columns(2)
    c1.metric("🍾 Botellas tapadas", st.session_state.C_CONTADOR)
    c2.metric("Estado ciclo",
              "ACTIVO" if st.session_state.M_CICLO_ACTIVO else "DETENIDO")

    st.markdown("**Temporizadores**")
    t1 = min(0.3, st.session_state.T1_elapsed)
    t2 = min(0.5, st.session_state.T2_elapsed)
    st.progress(t1 / 0.3, text=f"T1 estabilización: {t1:.2f}s / 0.30s")
    st.progress(t2 / 0.5, text=f"T2 prensado: {t2:.2f}s / 0.50s")

    st.markdown("**Pilotos**")
    pil = []
    if st.session_state.DO_PILOTO_AUTO: pil.append("🟢 AUTO")
    if st.session_state.DO_PILOTO_FALLO: pil.append("🔴 FALLO")
    if st.session_state.DO_PILOTO_CIL_ABAJO: pil.append("🟡 CIL. ABAJO")
    st.write("  ".join(pil) if pil else "⚫ Todos apagados")

# ---- Columna C: Log ----
with col_c:
    st.subheader("📜 Log de eventos")
    if st.session_state.log:
        st.markdown("\n".join(st.session_state.log[:18]))
    else:
        st.caption("Sin eventos todavía")

    st.divider()
    if st.button("🧹 Limpiar log", use_container_width=True):
        st.session_state.log = []
        st.rerun()
