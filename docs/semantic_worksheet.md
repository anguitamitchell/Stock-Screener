# Semantic Screening Worksheet

This worksheet turns the narrative view into a score that can sit beside the technical score.

## Core Themes

| Theme | Weight | What it rewards |
| --- | ---: | --- |
| Millennial spending power | 18 | Convenience, digital ecosystems, travel, wellness, premium brands, and online consumption. |
| Labor-supply advantage | 18 | Automation, productivity, staffing leverage, logistics leverage, and reduced labor dependency. |
| Enterprise AI leadership | 26 | AI infrastructure, cloud, chips, data tooling, enterprise software, cybersecurity, and mission-critical AI adoption. |
| Grid and data-center demand | 18 | Power infrastructure, data-center buildout, electrification, networking, cooling, and physical AI infrastructure. |
| Energy-security tailwinds | 20 | U.S. energy advantage, geopolitical supply security, reshoring, power demand, and strategic commodity exposure. |

Total possible score: 100.

## Current Method

Each company is mapped to one or more themes in `config/semantic_themes.json`.

Companies can also have an explicit research conviction score. This matters because a focused leader in one theme can be more attractive than a broad but shallow fit across several themes.

For now, this is a curated research map, not an AI-generated news summary. The goal is to make the narrative logic visible and editable before adding automated research.

Unmapped companies receive a first-pass heuristic score from ticker/name keyword rules in `semantic.py`. Curated entries in `semantic_themes.json` always override the heuristic fallback.

## Label Thresholds

| Score | Label |
| ---: | --- |
| 75-100 | narrative leader |
| 55-74 | aligned |
| 30-54 | partial fit |
| 0-29 | low fit |

Fallback-matched companies may also show `heuristic fit`, which means the company matched a theme by name/ticker but has not yet been individually researched.

## Research Sources To Expand Later

- Annual reports and 10-Ks
- Investor presentations
- Segment revenue trends
- Capex plans
- Earnings-call commentary
- Customer adoption evidence
- Theme-specific risk notes

## Initial Research Notes

- NVIDIA, Microsoft, Amazon, Alphabet, Broadcom, AMD, Cisco, and Oracle-like platforms should be evaluated heavily under enterprise AI infrastructure.
- Amazon, Apple, Costco, Walmart, Visa, Mastercard, Netflix, and Tesla have meaningful exposure to millennial/digital consumption.
- ExxonMobil and major energy companies belong in the energy-security theme.
- Caterpillar, GE Vernova-like infrastructure names, utilities, semis, networking, and data-center supply-chain firms belong in the grid/data-center demand theme.
- Automation and labor productivity should include companies using scale, software, logistics, robotics, or process automation to reduce labor intensity.
