# Addendum 4 (owner, 2026-11-26)

*A tidied record of requirements given on 2026-11-26: quotations of the owner's chat messages are restated as plain statements with the same meaning.*

The owner's requirement: the architecture is also documented with Mermaid diagrams that clearly
explain the platform's design ethos.

N. Open your entry with a short **design ethos**: five to eight principles, one sentence each, that
   a new engineer could use to predict a design choice (for example "a product is defined once";
   "the platform starts at the topic"). Every later section should be traceable to one of them.
O. Document the architecture with **Mermaid diagrams**, each fitting one screen, each with a
   one-paragraph reading guide. At minimum:
   1. the four layers and the product crossing them (flowchart);
   2. one product's journey from contract to served query, showing what is written by hand and
      what is generated (flowchart or sequence);
   3. the medallion flow with connector, config-driven transforms and the code escape hatch;
   4. the lifecycle states and their transitions (stateDiagram-v2);
   5. how a permission declared in a contract reaches the SQL endpoint and the REST API;
   6. today versus target for the crypto migration, side by side.
   Mermaid only, no ASCII art. Your plan must include a phase item that lands these as chapters
   under `docs/architecture/` and keeps them current.
