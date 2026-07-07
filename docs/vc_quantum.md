# VC相関の量子情報論的再定式化

ボラティリティ条件付き方向性相関（**VC相関** = Volatility-Conditioned
directional Correlation）を、量子相互情報量とエンタングルメント・エントロピー
の数学的枠組みで書き直す。実装は `quantum_vc.py`（numpy 非依存の純 Python）、
配信は `/api/vc_correlation`、可視化はダッシュボードの「量子相関」タブ。

---

## 1. 古典的な VC相関とは

2 資産 A, B のリターン系列 \(r_A(t), r_B(t)\) に対し、

- **V（条件づけ）**: 各時刻の**局所ボラティリティ** \(\sigma_X(t)\) でリターンを
  標準化し、平穏期と乱高下期を同じ土俵に載せる。
- **C（方向性）**: 標準化リターンの**符号（上げ／下げ）**の連動を測る。

素朴には \(\rho_{\mathrm{VC}} = \langle \mathrm{sgn}\,z_A \cdot \mathrm{sgn}\,z_B\rangle_w\)
（\(z_X = r_X/\sigma_X\)、\(w\) はボラ加重）という符号相関。これを情報幾何的に
リッチな量子形式へ持ち上げる。

---

## 2. 量子状態への符号化

### 2.1 標準化リターン（V）
\[
r_X(t) = \ln\frac{P_X(t)}{P_X(t-1)},\qquad
\sigma_X(t)\ \text{= EWMA 推定},\qquad
z_X(t) = \frac{r_X(t)}{\sigma_X(t)}.
\]
EWMA（RiskMetrics 流、\(\lambda=0.94\)）は
\(\sigma^2_t = \lambda\sigma^2_{t-1} + (1-\lambda)r_t^2\)。

### 2.2 1資産キュービット（C）
標準化リターンを Bloch 球の極角へ写像する：
\[
\theta_X(t) = \pi\, s\!\big(-k\, z_X(t)\big),\qquad
s(u) = \frac{1}{1+e^{-u}}\ (\text{ロジスティック}),
\]
\[
|\psi_X(t)\rangle = \cos\tfrac{\theta_X}{2}\,|\!\uparrow\rangle
                  + \sin\tfrac{\theta_X}{2}\,|\!\downarrow\rangle .
\]
強い上昇 \(z\to+\infty\) は \(|\!\uparrow\rangle\)、強い下落 \(z\to-\infty\) は
\(|\!\downarrow\rangle\)、静穏 \(z=0\) は赤道上の重ね合わせ
\(\tfrac{1}{\sqrt2}(|\!\uparrow\rangle+|\!\downarrow\rangle)\) に対応する。
\(k\)（`gain`、既定 2.0）は方向の鋭さを制御する。

### 2.3 各時刻の積状態
\[
|\Phi(t)\rangle = |\psi_A(t)\rangle \otimes |\psi_B(t)\rangle
\in \mathcal H_A\otimes\mathcal H_B\quad(\dim = 4).
\]
単一時刻では**分離可能（積）状態**であり、瞬間の相互情報量はゼロ。相関は
時間アンサンブルから立ち上がる。

---

## 3. アンサンブル密度行列

時間平均で混合状態を作る。乱高下期を強調するボラ加重
\(w(t)\propto\sigma_A(t)\,\sigma_B(t)\)（さらなる "V"）を用いる：
\[
\rho_{AB} = \frac{1}{\sum_t w(t)}\sum_t w(t)\,|\Phi(t)\rangle\langle\Phi(t)| .
\]
\(\rho_{AB}\) は \(4\times4\) のエルミート・半正定値・トレース 1。実振幅を採るので
実対称行列となり、非対角成分（コヒーレンス）は実数で残る。基底順は
\(|{\uparrow\uparrow}\rangle,|{\uparrow\downarrow}\rangle,
|{\downarrow\uparrow}\rangle,|{\downarrow\downarrow}\rangle\)。

積状態の凸結合なので \(\rho_{AB}\) は**分離可能**（古典相関）。すなわち
ネガティビティやエンタングルメント・オブ・フォーメーションは 0。真に「量子的」
な寄与は次節の l1 コヒーレンスと Schmidt スペクトルが捉える。

---

## 4. 量子情報量

### 4.1 縮約状態と von Neumann エントロピー
\[
\rho_A = \operatorname{Tr}_B \rho_{AB},\quad
\rho_B = \operatorname{Tr}_A \rho_{AB},\quad
S(\rho) = -\operatorname{Tr}\rho\log_2\rho = -\sum_i \lambda_i\log_2\lambda_i .
\]
\(2\times2\) は解析的に、\(4\times4\) は循環 Jacobi 法で固有値を得る。

### 4.2 量子相互情報量（＝再定式化した VC相関の大きさ）
\[
\boxed{\ \mathcal I(A\!:\!B) = S(\rho_A) + S(\rho_B) - S(\rho_{AB}) \ge 0\ }
\]
全相関（古典＋量子）を bits で測る。2 キュービット系では \(0\le \mathcal I\le 2\)。
正規化 \(\hat{\mathcal I} = \mathcal I/2 \in[0,1]\)。

### 4.3 方向を付す — 符号付き VC-Q 相関
\(\mathcal I\) は連動・逆連動を区別しない（どちらも情報を増やす）。方向性 "C" を
回復するため、ボラ加重符号相関 \(\rho_{\mathrm{sgn}}\) の符号を付与する：
\[
\boxed{\ \mathrm{VCQ} = \operatorname{sgn}(\rho_{\mathrm{sgn}})\cdot\hat{\mathcal I}
\in[-1,+1]\ }
\]
\(+1\) に近いほど強い順連動、\(-1\) に近いほど強い逆連動。

### 4.4 エンタングルメント・エントロピー（Schmidt）
平均振幅（コヒーレント平均場）行列
\(M_{ij} = \langle\, a^A_i\, a^B_j\,\rangle_w\)（\(2\times2\)）の特異値
\(\{\varsigma_k\}\) から Schmidt 係数 \(p_k = \varsigma_k^2/\sum\varsigma_k^2\) を作り、
\[
S_E = -\sum_k p_k\log_2 p_k .
\]
\(M\) がランク 1（完全連動、単一モード支配）で \(S_E=0\)、二つの特異値が拮抗
（独立）で \(S_E\to1\)。すなわち \(S_E\) は方向的連動の**モード集中度**を測る双対量。

### 4.5 l1 コヒーレンス（量子的寄与）
\[
C_{\ell_1}(\rho_{AB}) = \sum_{i\neq j}\lvert(\rho_{AB})_{ij}\rvert .
\]
分離可能性の下でも残る非対角の重ね合わせを定量化し、単なる古典的
分割表相関との差分を明示する。

---

## 5. 古典極限との整合

| 状況 | 期待 | 実装挙動 |
|---|---|---|
| 完全連動 \(r_A=r_B\) | \(\mathrm{VCQ}>0\), \(S_E\downarrow\) | ✅ 符号 +, Schmidt が単一モード支配 |
| 完全逆連動 \(r_A=-r_B\) | \(\mathrm{VCQ}<0\) | ✅ 符号 − |
| 独立 | \(\mathcal I\to0\) | ✅ \(\hat{\mathcal I}\approx0\) |

合成データでの検証は本文末の再現手順を参照。実データ（主要株価指数・日次）では
**時差の無い同期市場**（DAX × FTSE100）が最大の \(\mathrm{VCQ}\) を示し、
非同期な終値（日経 × S&P500 等）は小さくなる — 終値ベースのリード・ラグを
正しく反映する。

---

## 6. 実装と配信

- `quantum_vc.py`
  - `analyze_pair(prices_a, prices_b, lam=0.94, gain=2.0, vol_weight=True)`
    — 1 ペアの全指標を返す。
  - `correlation_matrix(histories)` — 複数系列のペアワイズ行列。日付は
    ペアごとに共通取引日で整列。
- `app.py` `/api/vc_correlation` — 主要株価指数（直近約 800 取引日）を集計。
- `templates/index.html`「量子相関」タブ — 符号付き VC-Q / 量子相互情報量の
  ヒートマップ（自動スケール配色）とペア別詳細表、数理枠組みの掲示。

### 再現（合成データ）
```python
import math, random, quantum_vc as q
random.seed(1)
mk = lambda rs: [100*math.exp(sum(rs[:i])) for i in range(len(rs)+1)]
base = [random.gauss(0, .01) for _ in range(800)]
print(q.analyze_pair(mk(base), mk(base))['vcq_correlation'])          # 順連動 > 0
print(q.analyze_pair(mk(base), mk([-x for x in base]))['vcq_correlation'])  # 逆連動 < 0
print(q.analyze_pair(mk(base), mk([random.gauss(0,.01) for _ in range(800)]))['quantum_mutual_info'])  # ≈ 0
```

---

## 7. 解釈上の注意

- ここでの \(\rho_{AB}\) は分離可能状態であり、金融時系列に literal な量子
  もつれが存在すると主張するものではない。量子情報量は**相関構造を記述する
  数学的枠組み**として用いている。
- \(\mathcal I\) の絶対値は符号化（`gain`）とボラ加重に依存する。ペア間の相対
  比較・時系列変化の追跡に用いるのが妥当で、ヒートマップは最大値で自動スケール
  している。
- 終値ベースのため異なるタイムゾーンの市場間はリード・ラグの影響を受ける。
  同期性を厳密化するには時刻整合（同一 UTC 断面）やリターンのラグ探索を要する。
