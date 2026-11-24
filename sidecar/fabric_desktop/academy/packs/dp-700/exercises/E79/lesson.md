# Sliding windows and warm episodes over freezer readings

Freezers send a temperature reading every few minutes. A reading above -15 °C is warm. Operations wants two things. The alarm fires when a freezer has 3 or more warm readings in the 10 minutes up to a warm reading, wherever those 10 minutes fall on the clock. The daily report lists each warm episode, which is a run of warm readings in a row that ends at the first reading that isn't warm.

## What you'll do

Count events in a sliding window that ends at each event, and group a run of matching events into one episode with gaps and islands.

## Sliding window or episode

- A sliding window has a fixed length and moves with the events. Here it ends at each warm reading and starts just after the moment 10 minutes earlier.
- A tumbling window is a fixed block on the clock. It can split a burst of readings in two.
- An episode has no fixed length. It lasts as long as the readings stay warm.

## How it runs here

Your answer is DuckDB SQL. It runs on this PC over a fixed set of stored readings, not over a live stream.

## Before you start

- E43

## What this run proves

- Your SQL ran on this PC with DuckDB over a fixed set of readings and produced the expected sliding windows and warm episodes.

## What it doesn't prove

- Windowing in a running stream. This runs a batch query over stored readings, not an eventstream, KQL or Spark structured streaming.
- Every output of a streaming sliding window. A streaming engine also emits a window when an event leaves it. Here a window is counted only when a warm reading arrives.
- Which end of a sliding window an eventstream or Stream Analytics counts as inside. Here the exercise sets the rule itself.
- Late readings and watermarks, which streaming engines add on top of these windows.
