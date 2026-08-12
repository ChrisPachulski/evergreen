# mermaid-diagrams — the diagram contract `flourish` enforces

A README's "how it works" is a claim about structure, and structure is the one claim prose is worst
at carrying. Flourish requires that claim to be drawn, in mermaid, checked into the doc — not
sketched in box-drawing characters that die on the first font change.

**Two rules are hard** (see `hard-goals/flourish.md`, goal 7). Everything else here is craft.

1. **Mermaid, not ASCII.** A fenced ` ```mermaid ` block. Never box-drawing art, never a
   pre-rendered image where the source could live in the file.
2. **Vertical, always.** `graph TD` or `flowchart TD` (`BT` where the argument genuinely flows
   upward). Never `LR`, never `RL`, and no `direction LR` inside a subgraph.

## Why vertical

A README is read in a narrow column — a phone, a split editor pane, a GitHub sidebar. An `LR`
diagram grows along the axis the reader has least of, so it either shrinks past legibility or
forces a horizontal scroll that hides half the argument. `TD` grows along the axis the page already
scrolls. The convention that pipelines "should" be `LR` comes from slide decks, which are landscape;
READMEs are portrait. Follow the page, not the deck.

Nodes at the same depth still sit side by side — that is rank, not direction, and it is fine.

## The ELK directive

Every flowchart opens with it, on the first line inside the fence:

```
%%{init: {"flowchart": {"defaultRenderer": "elk"}} }%%
```

ELK routes edges and spaces subgraphs far better than the default engine, and the difference is
most visible on exactly the shape a README needs — a few sources fanning into one spine. It applies
to `graph`/`flowchart` only; `sequenceDiagram`, `stateDiagram-v2`, and `erDiagram` bring their own
layout and need no directive.

## What earns a node

The diagram argues; it does not inventory.

- **Concrete names, always.** `aggregate_all builds 7 stacked grains` beats `Aggregation`. Real
  function, table, and column names are what make a diagram worth more than the paragraph above it.
- **Plain language first, technical second.** Lead each label with what the step *does* for a
  reader, then the identifier underneath via `<br>`. Someone skimming only the first lines should
  still follow the flow.
- **The removal test.** Delete half the nodes. If the remaining structure still teaches something,
  it is a diagram. If not, it was a bulleted list wearing boxes — write the list instead.
- **Ten to twenty nodes.** Past that, split it or collapse a branch into one node that names where
  the detail lives.

## Shapes carry meaning

| Shape | Syntax | Use for |
|---|---|---|
| Cylinder | `A[("…")]` | Data stores, warehouse tables |
| Parallelogram | `A[/"…"/]` | Inputs, hand-dropped files |
| Hexagon | `A{{"…"}}` | Preparation and transform steps |
| Diamond | `A{"…"}` | Decisions |
| Rectangle | `A["…"]` | Ordinary processes |
| Trapezoid | `A[\"…"/]` | Outputs, artifacts |

Solid `-->` for the primary path, dotted `-.->` for optional or conditional, and label any edge
whose reason is not obvious: `-.->|"optional"|`.

## Palette — dark-first, semantic names

Docs render on both themes, and a light-filled node with light text disappears on one of them. Use
near-black fills tinted toward a saturated stroke, with light text. Name classes for what they
*are*, never for their color.

```
classDef source    fill:#2a4858,stroke:#00f0ff,color:#e0e0e0
classDef staging   fill:#1a1a2a,stroke:#8888cc,color:#c0c0c0
classDef region    fill:#3a2a4a,stroke:#ff00aa,color:#e0e0e0
classDef modelling fill:#3a3a1a,stroke:#ffcc00,color:#e0e0e0
classDef output    fill:#1a3a2a,stroke:#00ff88,color:#e0e0e0
classDef constraint fill:#2a1a1a,stroke:#ff4444,color:#c0c0c0
```

Every node gets a class. An unstyled node reads as an oversight.

## Syntax traps that break the render

- **Quote every label.** `A["fn(x, y)"]`, never `A[fn(x, y)]` — bare parens and brackets are shape
  syntax.
- **Node IDs are alphanumeric plus underscore.** Punctuation goes in the label only.
- **No `N. ` line starts.** Mermaid parses `"1. Read"` as a markdown list and warns. Write `"1: Read"`.
- **Two `<br>` breaks per node, maximum.** Three or more short phrases also parse as a list.

## Prove it renders

Do not ship a diagram you have not rendered. It costs one command:

```sh
npx -y @mermaid-js/mermaid-cli -i README.md -o /tmp/check.md
```

A syntax error fails loudly here and silently on GitHub, where it degrades to a raw code block.
Render it, look at the output, then commit. `mmdc` writes its artifacts next to the output path —
point that at a scratch directory, never into the repo.
