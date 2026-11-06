【患者变量摘要】
已提供：diabetes_type=1型；dr_severity=重度NPDR；sex=男；age=45
推断（高置信）：diabetes_onset_age=12（原文"12岁前发病"）
不适用：pregnancy_with_diabetes_status（na_rule: pregnancy_with_diabetes_status = N/A）
缺失：dme_type；vision_symptom；visual_acuity

【命中规则】
1. SCREEN-009 · 随访频率应＜3个月
   条件：dr_severity = 重度NPDR（已提供 重度NPDR）
   溯源：重度NPDR患者随访频率应＜3个月

2. SCREEN-002 · 起病5年内筛查，之后应每年随诊1次
   条件：diabetes_type = 1型（已提供 1型） 且 diabetes_onset_age >= 12（推断值 12）
   溯源：12岁之后发病者，起病5年内筛查，之后应每年随诊1次
   ⚠ 基于推断值，建议复核。

【无法判定规则】
- SCREEN-011 · CI-DME患者每月随访1次
  缺失变量：dme_type（missing_from_input）
  溯源：CI-DME患者每月随访1次

- SCREEN-012 · NCI-DME患者每3个月随访1次
  缺失变量：dme_type（missing_from_input）
  溯源：NCI-DME患者每3个月随访1次

- SCREEN-013 · 及时到有眼底病医疗资源的医院就诊
  缺失变量：visual_acuity（missing_from_input）；vision_symptom（missing_from_input）；vision_symptom（missing_from_input）
  溯源：一旦视力≤0.7（20/30或4.8）或者患者出现突发的视力下降以及视物模糊应及时到有眼底病医疗资源的医院就诊

【其他规则（折叠）】
- SCREEN-004 · 应在妊娠或第1次产检时筛查，妊娠后每3个月筛查，产后1年时筛查：na_rule: pregnancy_with_diabetes_status = N/A
- SCREEN-005 · 怀孕期间不需要进行眼底检查：na_rule: pregnancy_with_diabetes_status = N/A
- SCREEN-001 · 自12岁起每年筛查：diabetes_type = 1型（已提供 1型） 且 diabetes_onset_age < 12（推断值 12）
- SCREEN-003 · 应在确诊时开始筛查眼底病变，每年随诊1次：diabetes_type = 2型（已提供 1型）
- SCREEN-006 · 至少每1～2年复查1次：dr_severity = 无DR（已提供 重度NPDR）
- SCREEN-007 · 每6～12个月复查1次：dr_severity = 轻度NPDR（已提供 重度NPDR）
- SCREEN-008 · 每3～6个月复查1次：dr_severity = 中度NPDR（已提供 重度NPDR）
- SCREEN-010 · 随访频率可考虑1个月：dr_severity = PDR（已提供 重度NPDR）
