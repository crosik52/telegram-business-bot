---
name: slides
description: Create strategic HTML presentations with Chart.js, design tokens, responsive layouts, copywriting formulas, and contextual slide strategies. Use for marketing presentations, pitch decks, data-driven slides, and strategic slide design.
argument-hint: "[topic] [slide-count]"
---

# Slides

Strategic HTML presentation design with data visualization.

## When to Use

- Marketing presentations and pitch decks
- Data-driven slides with Chart.js
- Strategic slide design with layout patterns
- Copywriting-optimized presentation content

## Creation Workflow

1. **Parse goal/context** — understand audience, purpose, slide count
2. **Select strategy** — choose deck structure + emotion arc
3. **For each slide** — choose layout, typography scale, color treatment, animation
4. **Generate HTML** — with design tokens and Chart.js where needed
5. **Validate** — token compliance (no hardcoded hex values)

## Slide Requirements

ALL slides MUST:
1. Use CSS variables (design tokens) — no hardcoded hex colors
2. Use Chart.js for charts (NOT CSS-only bars)
3. Include keyboard navigation (arrow keys, click, progress bar)
4. Center align content
5. Focus on persuasion/conversion

## Chart.js Integration

```html
<script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.1/dist/chart.umd.min.js"></script>

<canvas id="revenueChart"></canvas>
<script>
new Chart(document.getElementById('revenueChart'), {
    type: 'line',
    data: {
        labels: ['Q1', 'Q2', 'Q3', 'Q4'],
        datasets: [{
            data: [5, 12, 28, 45],
            borderColor: 'var(--color-primary)',
            backgroundColor: 'rgba(0,0,0,0.1)',
            fill: true,
            tension: 0.4
        }]
    }
});
</script>
```

## Token Compliance

```css
/* CORRECT — uses token */
background: var(--slide-bg);
color: var(--color-primary);

/* WRONG — hardcoded */
background: #0D0D0D;
color: #FF6B6B;
```

## Copywriting Formulas

- **PAS** — Problem, Agitate, Solution (for pain-point slides)
- **AIDA** — Attention, Interest, Desire, Action (for pitch flow)
- **FAB** — Feature, Advantage, Benefit (for product slides)

## Pattern Breaking (Duarte Sparkline)

Premium decks alternate emotions for engagement:
```
"What Is" (frustration) ↔ "What Could Be" (hope)
```
Apply pattern breaks at ~1/3 and ~2/3 positions in the deck.
