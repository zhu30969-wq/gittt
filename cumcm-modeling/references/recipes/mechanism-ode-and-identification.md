# ODE、传热与参数辨识配方

本配方用于常微分方程、方法线离散的一维传热、由观测反演机理参数，以及参数不确定性向输出的传播。先确认守恒关系、边界/初值、量纲和可识别参数组合，再谈拟合精度；最细网格值和最小残差都不是自动可发表的结论。

## 1. 结构签名

- 状态量、独立变量、参数、初值/边界条件和单位有明确物理意义；
- 输出来自 ODE，或可用空间离散转成 ODE 组的一维抛物型传热方程；
- 至少一问需要首次达到阈值的事件时刻、温度场、参数反演或不确定性区间；
- 数据可拆成互不重叠的拟合集与留出集；
- 存在解析解、守恒量、极限情形或高精度参考解中的至少一种独立真值源。

## 2. 不适用条件

- 只有相关性数据，状态方程和参数含义无法由题面或可辩护假设建立；
- 空间维度、材料界面或几何效应不能被一维化且误差无法验证；
- 参数只以乘积、比值或同一线性组合出现，却仍要求分别给出唯一点估计；
- 拟合数据与验证数据无法区分，又准备把拟合残差称为预测误差；
- 目标主要是离散组合选择，应改用优化配方并把机理求解器作为评价函数。

## 3. 最简可运行基线

先使用常系数、集中参数或较粗但稳定的一维模型；对 ODE 显式给出 `rtol`、`atol` 和方法，对传热至少做三层等比例网格细化。若题面允许解析极限，先跑解析 oracle。参数辨识先用最少参数和固定种子多起点 `least_squares`，同时报告拟合残差、留出残差、Jacobian 条件数和列相关，而不是只报一个最优参数表。

## 4. 标准数学表达

状态方程写成

\[
\dot{\mathbf y}=f(t,\mathbf y;\boldsymbol\theta),\qquad
\mathbf y(t_0)=\mathbf y_0.
\]

一维传热基线为

\[
\frac{\partial T}{\partial t}=\alpha\frac{\partial^2T}{\partial x^2},
\]

边界可取给定温度，或 `-k\partial T/\partial n=h(T-T_\infty(x,t))` 的 Robin 对流条件。参数辨识只在拟合集 `F` 上求

\[
\hat{\boldsymbol\theta}=
\arg\min_{\boldsymbol\theta\in\Theta}
\sum_{i\in F}\left[y_i-g(x_i;\boldsymbol\theta)\right]^2,
\]

并在互斥留出集 `H` 上单独计算残差。若灵敏度 Jacobian 病态或列近共线，只能报告区间与可识别组合，不能报告各参数唯一点估计。

## 5. 数值预算与方法选择

- `RK45`/`DOP853` 用于非刚性问题，`Radau`/`BDF`/`LSODA` 用于已知刚性问题；非刚性方法失败或步数超过预登记阈值时只标 `stiffness_suspected`，不静默换法；
- 事件用 `solve_ivp events` 定位，不从稀疏输出网格肉眼读取首次越界时刻；
- 传热空间网格至少三层等比例细化，理论二阶离散应以 Richardson 实测阶核对；
- 参数多起点数、种子、边界、最大函数调用数、条件数阈值和列相关阈值在看结果前登记；
- Sobol 样本量取 2 的幂，蒙特卡洛结论同时报告 MC 标准误；MCSE 超过预登记精度时，保留原始估计但不暴露可报告点估计。

## 6. 统一接口

可运行示例见 [mechanism_ode_and_identification.py](examples/mechanism_ode_and_identification.py)：

```powershell
python -X utf8 cumcm-modeling/references/recipes/examples/mechanism_ode_and_identification.py
```

接口实现见 [mechanism_toolkit](../../assets/mechanism-toolkit/mechanism_toolkit/__init__.py)：

- `integrate_ode` 返回方法、步数、事件、函数调用与刚性嫌疑；
- `solve_heat_1d` 支持 Dirichlet/Robin 边界及随位置、时间变化的环境温度；
- `identify_parameters` 返回固定种子多起点记录、独立的 `fit_residual`/`holdout_residual` 和可识别性结构；
- `propagate_uncertainty` 返回 MCSE，并在样本量不足时清空 `reportable_estimate`；
- `verify_analytic_oracle` 内置指数衰减、刚性 Robertson 高精度参考和 Dirichlet 热传导级数解。

`MechanismResult` 只有在 `ConvergenceCertificate` 至少包含三层且实测阶与理论阶在预登记容差内时才暴露 `converged_value`。`IdentificationResult` 在条件数或列相关超阈值时，无条件把 `point_estimate` 清为 `None`，保留无界/有限区间和 `identifiable_combinations`。

## 7. 契约登记

机理模型通常选 `simulation`；同时求决策变量时用 `hybrid` 并在 `validation_facets` 中加入 `simulation` 与 `optimization`。参数辨识在 `validation_plan` 中显式声明 `identifiability`、`residual_diagnostics` 与 `uncertainty`；结果的 identifiability diagnostic 用 `parameter_identification` 登记点估计是否被抑制、参数区间、可识别组合、拟合/留出残差、Jacobian 条件数和列相关阈值。

不可识别时，final 定量 claim 只能绑定可识别组合或区间指标。手工绕过工具包重新写入参数点估计，并把相应 metric 绑定到 final claim，会触发 `UNIDENTIFIABLE_POINT_ESTIMATE_CLAIM` 并使结果失去 eligibility。

## 8. 本轮实测失败模式

1. **最细网格冒充收敛值**：结构测试给出三层一阶误差序列却声明理论二阶；实测阶偏差超阈值后，最细层值仍保留，但 `converged_value` 被清空。
2. **层级不足**：只有两层数值无法估计 Richardson 阶，返回 `insufficient_levels`，不会签发收敛值。
3. **乘积参数不可分**：合成观测只含 `k1*k2`，多起点都能获得近零拟合与留出残差，但 Jacobian 列完全共线；工具包清空两个独立点估计，仅保留 `k1*k2`。
4. **非刚性方法步数膨胀**：将预登记步数阈值压低后，`RK45` 返回 `stiffness_suspected=true` 且方法名仍为 `RK45`，证明接口没有悄悄换求解器。
5. **蒙特卡洛样本不足**：零精度预算下 MCSE 非零，`sample_size_sufficient=false`，`reportable_estimate` 被清空。

这些是本轮自动化验证实际触发的行为，不是固定阈值建议。真实赛题应按单位、目标精度和计算预算重新预登记数值。

## 9. 独立验证与解释边界

先用解析/高精度 oracle 验证积分器与空间离散，再检查守恒、边界、量纲、网格阶和参数可识别性。拟合良好只说明某些参数组合能解释拟合集；只有留出残差、稳定区间和可识别性共同成立时，才可在声明范围内讨论参数点值。

可以声明：指定方程、边界、容差和网格下的数值结果；经三层证书支持的收敛值；留出集上的误差；可识别参数组合；满足预登记 MCSE 精度的统计量。

不能声明：最细网格值必然是真值；求解器 success 等于模型正确；病态 Jacobian 下每个参数唯一；拟合残差等于样本外误差；单次 Monte Carlo 点值足以支持精细结论。
