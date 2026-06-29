"""Job source adapters. Each adapter ingests jobs into the pipeline by building
`Job` objects and calling `store.upsert(...)`; everything downstream
(tailoring, scoring, render, review, tracking) is source-agnostic."""
