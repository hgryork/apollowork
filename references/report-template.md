# Report Template

Use this template when the user asks for a full report, a long-form writeup, or a presentation-ready narrative.

## Required sections

1. Data quality and confidence
2. Global full-link latency summary
3. Module and handoff summary
4. RT and Data Age anomaly overview
5. Drop location distribution
6. Drop timeline and latency timeline alignment
7. Representative frame-level attribution
8. If relevant, scenario or time-window slice analysis
9. Conclusions and next actions

## Required figures

- RT raw vs steady-state
- Data Age raw vs steady-state
- Planning key chart, including a trimmed or zoomed view when startup outliers distort the axis
- Drop timeline aligned with RT or Data Age timeline
- If drop diagnosis matters, a stacked drop-by-stage timeline

## Report rules

- Every chart must have a one-paragraph explanation.
- Every anomaly section must include numeric evidence.
- If the report uses derived tables instead of rebuilding from raw tables, say so explicitly.
- If confidence is reduced by poor coverage or incomplete paths, say so near the top instead of hiding it in a footnote.
