"""
VC相関（ボラティリティ条件付き方向性相関）の量子情報論的再定式化
==================================================================

古典的な "VC相関" — 局所ボラティリティで条件づけた 2 資産のリターンの
「方向（符号）」の連動 — を、量子相互情報量とエンタングルメント・
エントロピーの数学的枠組みで書き直したもの。numpy 非依存の純 Python 実装。

数学的枠組み（詳細は docs/vc_quantum.md）
------------------------------------------------
1. ログリターン      r_X(t) = ln P_X(t) / P_X(t-1)
2. 局所ボラティリティ σ_X(t) を EWMA で推定（＝条件づけの "V"）
3. 標準化リターン    z_X(t) = r_X(t) / σ_X(t)     … ボラで条件づけ
4. 1 資産キュービット符号化（Bloch 球）
       θ_X(t) = π · s(-k · z_X(t)),   s = ロジスティック関数
       |ψ_X(t)⟩ = cos(θ/2)|↑⟩ + sin(θ/2)|↓⟩
   強い上昇 → |↑⟩、強い下落 → |↓⟩、静穏 → 赤道上の重ね合わせ（方向の "C"）
5. 各時刻の積状態  |Φ(t)⟩ = |ψ_A(t)⟩ ⊗ |ψ_B(t)⟩  （4 次元）
6. 時間アンサンブルの密度行列（相関はアンサンブルから生じる）
       ρ_AB = Σ_t w(t) |Φ(t)⟩⟨Φ(t)|,  w(t) ∝ σ_A(t)σ_B(t)（乱高下期を強調）
7. 量子情報量
       縮約状態  ρ_A = Tr_B ρ_AB,  ρ_B = Tr_A ρ_AB
       von Neumann エントロピー S(ρ) = -Σ λ_i log2 λ_i
       量子相互情報量（＝再定式化された VC 相関の大きさ）
           I(A:B) = S(ρ_A) + S(ρ_B) - S(ρ_AB) ≥ 0
       エンタングルメント（Schmidt）エントロピー：平均振幅行列の SVD から
       l1 コヒーレンス：ρ_AB の非対角成分の総和（量子的な寄与）
"""

from __future__ import annotations
import math

LOG2 = math.log(2.0)
EPS = 1e-12


# ---------- 基本統計 ----------

def log_returns(prices):
    """価格列 → ログリターン列。0 以下や欠損はスキップ。"""
    out = []
    for i in range(1, len(prices)):
        p0, p1 = prices[i - 1], prices[i]
        if p0 and p1 and p0 > 0 and p1 > 0:
            out.append(math.log(p1 / p0))
        else:
            out.append(0.0)
    return out


def ewma_vol(returns, lam=0.94, floor=1e-6):
    """指数加重移動平均によるボラティリティ σ(t) 列。RiskMetrics 流。"""
    if not returns:
        return []
    # 初期分散はサンプル分散
    m = sum(returns) / len(returns)
    var = sum((r - m) ** 2 for r in returns) / max(len(returns) - 1, 1)
    vols = []
    for r in returns:
        var = lam * var + (1.0 - lam) * r * r
        vols.append(math.sqrt(max(var, floor * floor)))
    return vols


def logistic(x):
    if x >= 0:
        z = math.exp(-x)
        return 1.0 / (1.0 + z)
    z = math.exp(x)
    return z / (1.0 + z)


# ---------- キュービット符号化 ----------

def qubit_amp(z, gain=2.0):
    """標準化リターン z → 実振幅 (a_up, a_down)。

    θ = π · s(-gain · z):  z→+∞ で θ→0（|↑⟩）、z→-∞ で θ→π（|↓⟩）、
    z=0 で θ=π/2（赤道上の重ね合わせ）。
    """
    theta = math.pi * logistic(-gain * z)
    return math.cos(theta / 2.0), math.sin(theta / 2.0)


# ---------- 密度行列の構築 ----------

def build_density(z_a, vol_a, z_b, vol_b, gain=2.0, vol_weight=True):
    """時間アンサンブルから 4x4 密度行列 ρ_AB（実対称）と平均振幅行列 M(2x2)。

    基底順: |00>,|01>,|10>,|11>（第1添字=A, 第2添字=B, 0=↑,1=↓）。
    戻り値: (rho4x4, M2x2, weighted_sign_corr)
    """
    n = min(len(z_a), len(z_b))
    rho = [[0.0] * 4 for _ in range(4)]
    M = [[0.0, 0.0], [0.0, 0.0]]
    wsum = 0.0
    sign_num = 0.0
    for t in range(n):
        za, zb = z_a[t], z_b[t]
        a0, a1 = qubit_amp(za, gain)
        b0, b1 = qubit_amp(zb, gain)
        # 重み: 両資産のボラの積（乱高下期を強調＝さらなるボラ条件づけ）
        w = (vol_a[t] * vol_b[t]) if vol_weight else 1.0
        if w <= 0:
            continue
        v = (a0 * b0, a0 * b1, a1 * b0, a1 * b1)  # kron(ψ_A, ψ_B)
        for i in range(4):
            wi = w * v[i]
            row = rho[i]
            for j in range(4):
                row[j] += wi * v[j]
        # 平均振幅（コヒーレント平均場）行列
        M[0][0] += w * a0 * b0
        M[0][1] += w * a0 * b1
        M[1][0] += w * a1 * b0
        M[1][1] += w * a1 * b1
        # 重み付き符号相関（方向性の古典的符号）
        sa = 1.0 if za > 0 else (-1.0 if za < 0 else 0.0)
        sb = 1.0 if zb > 0 else (-1.0 if zb < 0 else 0.0)
        sign_num += w * sa * sb
        wsum += w
    if wsum <= 0:
        return None, None, 0.0
    for i in range(4):
        for j in range(4):
            rho[i][j] /= wsum
    for i in range(2):
        for j in range(2):
            M[i][j] /= wsum
    return rho, M, sign_num / wsum


# ---------- 部分トレース ----------

def partial_trace_A(rho):
    """ρ_A = Tr_B ρ_AB（B をトレースアウト）。idx = 2*a + b。"""
    rA = [[0.0, 0.0], [0.0, 0.0]]
    for a in range(2):
        for ap in range(2):
            s = 0.0
            for b in range(2):
                s += rho[2 * a + b][2 * ap + b]
            rA[a][ap] = s
    return rA


def partial_trace_B(rho):
    """ρ_B = Tr_A ρ_AB（A をトレースアウト）。"""
    rB = [[0.0, 0.0], [0.0, 0.0]]
    for b in range(2):
        for bp in range(2):
            s = 0.0
            for a in range(2):
                s += rho[2 * a + b][2 * a + bp]
            rB[b][bp] = s
    return rB


# ---------- 固有値 ----------

def eig2x2_sym(m):
    """実対称 2x2 行列の固有値（降順）。"""
    a, b, d = m[0][0], m[0][1], m[1][1]
    tr = a + d
    diff = math.sqrt(max((a - d) ** 2 + 4.0 * b * b, 0.0))
    return [(tr + diff) / 2.0, (tr - diff) / 2.0]


def eig_sym_jacobi(mat, iters=100, tol=1e-14):
    """実対称行列の固有値（循環 Jacobi 法）。任意サイズ対応。"""
    n = len(mat)
    a = [row[:] for row in mat]
    for _ in range(iters):
        off = 0.0
        p, q = 0, 1
        for i in range(n):
            for j in range(i + 1, n):
                if abs(a[i][j]) > off:
                    off = abs(a[i][j])
                    p, q = i, j
        if off < tol:
            break
        app, aqq, apq = a[p][p], a[q][q], a[p][q]
        if abs(apq) < tol:
            break
        phi = 0.5 * math.atan2(2.0 * apq, aqq - app)
        c, s = math.cos(phi), math.sin(phi)
        for k in range(n):
            akp, akq = a[k][p], a[k][q]
            a[k][p] = c * akp - s * akq
            a[k][q] = s * akp + c * akq
        for k in range(n):
            akp, akq = a[p][k], a[q][k]
            a[p][k] = c * akp - s * akq
            a[q][k] = s * akp + c * akq
    return sorted((a[i][i] for i in range(n)), reverse=True)


# ---------- エントロピー ----------

def von_neumann_entropy(eigvals):
    """S = -Σ λ log2 λ（bits）。負の数値誤差はクリップ。"""
    s = 0.0
    for lam in eigvals:
        if lam > EPS:
            s -= lam * math.log(lam) / LOG2
    return s


def schmidt_entropy(M):
    """平均振幅行列 M(2x2) の SVD からエンタングルメント・エントロピー。

    特異値^2 を正規化した Schmidt 係数 p_k のエントロピー。
    M がランク1（完全連動）なら S_E=0、バランス型（独立）なら S_E→1。
    """
    # 特異値^2 = M^T M の固有値
    mtm = [
        [M[0][0] * M[0][0] + M[1][0] * M[1][0],
         M[0][0] * M[0][1] + M[1][0] * M[1][1]],
        [M[0][1] * M[0][0] + M[1][1] * M[1][0],
         M[0][1] * M[0][1] + M[1][1] * M[1][1]],
    ]
    ev = eig2x2_sym(mtm)
    tot = sum(max(e, 0.0) for e in ev)
    if tot <= EPS:
        return 0.0, [0.0, 0.0]
    p = [max(e, 0.0) / tot for e in ev]
    return von_neumann_entropy(p), p


def l1_coherence(rho):
    """ρ の非対角成分の絶対値和（l1 コヒーレンス）。量子的寄与の指標。"""
    n = len(rho)
    c = 0.0
    for i in range(n):
        for j in range(n):
            if i != j:
                c += abs(rho[i][j])
    return c


# ---------- ペア解析 ----------

def analyze_pair(prices_a, prices_b, lam=0.94, gain=2.0, vol_weight=True):
    """2 資産の価格列 → 量子情報論的 VC 相関の一式。

    prices_a, prices_b は共通日付で整列済みの終値列（長さ一致）を想定。
    """
    ra = log_returns(prices_a)
    rb = log_returns(prices_b)
    n = min(len(ra), len(rb))
    ra, rb = ra[:n], rb[:n]
    if n < 8:
        return None
    va = ewma_vol(ra, lam)
    vb = ewma_vol(rb, lam)
    za = [ra[i] / va[i] for i in range(n)]
    zb = [rb[i] / vb[i] for i in range(n)]

    rho, M, sign_corr = build_density(za, va, zb, vb, gain, vol_weight)
    if rho is None:
        return None

    rA = partial_trace_A(rho)
    rB = partial_trace_B(rho)
    S_A = von_neumann_entropy(eig2x2_sym(rA))
    S_B = von_neumann_entropy(eig2x2_sym(rB))
    S_AB = von_neumann_entropy(eig_sym_jacobi(rho))
    qmi = max(S_A + S_B - S_AB, 0.0)          # 量子相互情報量（bits）
    qmi_norm = min(qmi / 2.0, 1.0)             # 0..1 に正規化（2 qubit の上限=2 bits）
    S_E, schmidt = schmidt_entropy(M)          # エンタングルメント・エントロピー
    coh = l1_coherence(rho)

    # 方向性: 符号相関の符号を量子相互情報量の大きさに付与
    direction = 1.0 if sign_corr > 0 else (-1.0 if sign_corr < 0 else 0.0)
    vcq = direction * qmi_norm                 # 符号付き VC-Q 相関 (-1..1)

    # 参考: 古典 Pearson（標準化リターン）
    pear = _pearson(za, zb)

    return {
        'n': n,
        'quantum_mutual_info': round(qmi, 4),
        'quantum_mutual_info_norm': round(qmi_norm, 4),
        'entanglement_entropy': round(S_E, 4),
        'schmidt': [round(x, 4) for x in schmidt],
        'entropy_A': round(S_A, 4),
        'entropy_B': round(S_B, 4),
        'entropy_AB': round(S_AB, 4),
        'l1_coherence': round(coh, 4),
        'sign_corr': round(sign_corr, 4),
        'vcq_correlation': round(vcq, 4),
        'pearson': round(pear, 4),
    }


def _pearson(x, y):
    n = min(len(x), len(y))
    if n < 2:
        return 0.0
    mx = sum(x[:n]) / n
    my = sum(y[:n]) / n
    sxy = sxx = syy = 0.0
    for i in range(n):
        dx, dy = x[i] - mx, y[i] - my
        sxy += dx * dy
        sxx += dx * dx
        syy += dy * dy
    d = math.sqrt(sxx * syy)
    return sxy / d if d > EPS else 0.0


# ---------- 複数系列の整列と行列計算 ----------

def align_pair(hist_a, hist_b):
    """[[date, close], ...] 2 本を共通日付で整列。(prices_a, prices_b) を返す。"""
    map_b = {d: v for d, v in hist_b}
    pa, pb = [], []
    for d, v in hist_a:
        if d in map_b:
            pa.append(v)
            pb.append(map_b[d])
    return pa, pb


def correlation_matrix(histories, lam=0.94, gain=2.0, vol_weight=True):
    """histories: {key: [[date, close], ...]} → ペアごとの解析結果行列。

    戻り値: {'keys': [...], 'pairs': {(i,j): result}, 'matrix': [[vcq]]}
    """
    keys = list(histories.keys())
    m = len(keys)
    matrix = [[None] * m for _ in range(m)]
    qmi_mat = [[None] * m for _ in range(m)]
    pairs = {}
    for i in range(m):
        matrix[i][i] = 1.0
        qmi_mat[i][i] = 1.0
        for j in range(i + 1, m):
            pa, pb = align_pair(histories[keys[i]], histories[keys[j]])
            res = analyze_pair(pa, pb, lam, gain, vol_weight) if len(pa) >= 9 else None
            pairs[f'{i},{j}'] = res
            v = res['vcq_correlation'] if res else None
            q = res['quantum_mutual_info_norm'] if res else None
            matrix[i][j] = matrix[j][i] = v
            qmi_mat[i][j] = qmi_mat[j][i] = q
    return {'keys': keys, 'matrix': matrix, 'qmi_matrix': qmi_mat, 'pairs': pairs}
