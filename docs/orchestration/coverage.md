# Local pipeline coverage (Preview)

This page lists every Data Factory pipeline activity type the local runner knows and every function in Microsoft's [pipeline expression reference](https://learn.microsoft.com/fabric/data-factory/expression-language), with what a local run does with each. It is generated from the runner's activity registry and expression evaluator (`sidecar/fabric_desktop/orchestration/coverage.py`), and a test fails when it is out of date. Nothing here has been compared with a run in Fabric yet.

## Activities

28 activity types: 15 runs locally, 0 simulated, 5 refused, 8 Fabric only. A type the registry doesn't know is refused too: it is kept in the pipeline as it is, but not checked or run. See [activity compatibility](activity-registry.md) for each type's properties, sign-in and side effects.

- **runs locally:** a tested adapter runs it on this PC.
- **simulated:** it runs against fixtures or a stand-in, with no real side effects.
- **refused:** a local run checks it (structure, required properties) but can't run it; give it a mock.
- **Fabric only:** it only runs in Fabric or the Azure service it calls, and always uses capacity.

| Activity | Type | Status | Evidence | Planned |
| --- | --- | --- | --- | --- |
| Append Variable | `AppendVariable` | runs locally | unit tests |  |
| KQL | `AzureDataExplorerCommand` | Fabric only | unit tests |  |
| Azure Function | `AzureFunctionActivity` | Fabric only | unit tests |  |
| Copy data | `Copy` | refused | unit tests | later (not scheduled) |
| Azure Databricks notebook | `DatabricksNotebook` | Fabric only | unit tests |  |
| Delete data | `Delete` | refused | unit tests | later (not scheduled) |
| Execute pipeline (Data Factory) | `ExecutePipeline` | runs locally | unit tests |  |
| Fail | `Fail` | runs locally | unit tests |  |
| Filter | `Filter` | runs locally | unit tests |  |
| ForEach | `ForEach` | runs locally | unit tests |  |
| Get Metadata | `GetMetadata` | runs locally | unit tests |  |
| If Condition | `IfCondition` | runs locally | unit tests |  |
| Invoke pipeline | `InvokePipeline` | runs locally | unit tests |  |
| Lookup | `Lookup` | runs locally | unit tests |  |
| Office 365 Outlook | `Office365Outlook` | Fabric only | unit tests |  |
| Semantic model refresh | `PBISemanticModelRefresh` | Fabric only | unit tests |  |
| Dataflow Gen2 | `RefreshDataflow` | Fabric only | unit tests |  |
| Script | `Script` | runs locally | unit tests |  |
| Set Variable | `SetVariable` | runs locally | unit tests |  |
| Spark job definition | `SparkJobDefinition` | Fabric only | unit tests |  |
| Stored procedure | `SqlServerStoredProcedure` | refused | unit tests |  |
| Switch | `Switch` | runs locally | unit tests |  |
| Teams | `Teams` | Fabric only | unit tests |  |
| Notebook | `TridentNotebook`, `Notebook` | runs locally | unit tests |  |
| Until | `Until` | runs locally | unit tests |  |
| Wait | `Wait` | runs locally | unit tests |  |
| Web | `WebActivity` | refused | unit tests | later (not scheduled) |
| Webhook | `WebHook` | refused | unit tests | later (not scheduled) |

## Expression functions

88 functions: 50 runs, 0 differs, 2 fails, 6 not checked, 30 refused.

- **runs:** it gives the reference's value for the reference's own example.
- **differs:** it runs but gives another value for that example, so check results that use it in Fabric.
- **fails:** the reference's own example stops with an error here, so an expression like it fails the activity.
- **not checked:** it runs, but its value changes from run to run, or it reads the run itself, so there is no value to compare.
- **refused:** the activity stops with `<name>() isn't supported in local runs yet`. Nothing runs with a guessed value.

### String

| Function | Status | Example | Reference value | Value here |
| --- | --- | --- | --- | --- |
| `concat` | runs | `concat('Hello', 'World')` | "HelloWorld" |  |
| `endsWith` | runs | `endsWith('hello world', 'world')` | true |  |
| `guid` | not checked |  |  |  |
| `indexOf` | runs | `indexOf('hello world', 'world')` | 6 |  |
| `lastIndexOf` | runs | `lastIndexOf('hello world', 'world')` | 6 |  |
| `replace` | runs | `replace('the old string', 'old', 'new')` | "the new string" |  |
| `split` | runs | `split('a_b_c', '_')` | ["a", "b", "c"] |  |
| `startsWith` | runs | `startsWith('hello world', 'hello')` | true |  |
| `substring` | runs | `substring('hello world', 6, 5)` | "world" |  |
| `toLower` | runs | `toLower('Hello World')` | "hello world" |  |
| `toUpper` | runs | `toUpper('Hello World')` | "HELLO WORLD" |  |
| `trim` | runs | `trim(' Hello World  ')` | "Hello World" |  |

### Collection

| Function | Status | Example | Reference value | Value here |
| --- | --- | --- | --- | --- |
| `contains` | runs | `contains('hello world', 'world')` | true |  |
| `empty` | runs | `empty('')` | true |  |
| `first` | runs | `first('hello')` | "h" |  |
| `intersection` | runs | `intersection(createArray(1, 2, 3), createArray(101, 2, 1, 10), createArray(6, 8, 1, 2))` | [1, 2] |  |
| `join` | runs | `join(createArray('a', 'b', 'c'), '.')` | "a.b.c" |  |
| `last` | runs | `last('abcd')` | "d" |  |
| `length` | runs | `length('abcd')` | 4 |  |
| `skip` | runs | `skip(createArray(0, 1, 2, 3), 1)` | [1, 2, 3] |  |
| `take` | runs | `take('abcde', 3)` | "abc" |  |
| `union` | runs | `union(createArray(1, 2, 3), createArray(1, 2, 10, 101))` | [1, 2, 3, 10, 101] |  |

### Logical

| Function | Status | Example | Reference value | Value here |
| --- | --- | --- | --- | --- |
| `and` | runs | `and(true, true)` | true |  |
| `equals` | runs | `equals(true, 1)` | true |  |
| `greater` | runs | `greater(10, 5)` | true |  |
| `greaterOrEquals` | runs | `greaterOrEquals(5, 5)` | true |  |
| `if` | runs | `if(equals(1, 1), 'yes', 'no')` | "yes" |  |
| `less` | runs | `less(5, 10)` | true |  |
| `lessOrEquals` | runs | `lessOrEquals(5, 10)` | true |  |
| `not` | runs | `not(false)` | true |  |
| `or` | runs | `or(false, true)` | true |  |

### Conversion

| Function | Status | Example | Reference value | Value here |
| --- | --- | --- | --- | --- |
| `array` | runs | `array('hello')` | ["hello"] |  |
| `base64` | refused |  |  |  |
| `base64ToBinary` | refused |  |  |  |
| `base64ToString` | refused |  |  |  |
| `binary` | refused |  |  |  |
| `bool` | runs | `bool(1)` | true |  |
| `coalesce` | runs | `coalesce(null, true, false)` | true |  |
| `createArray` | runs | `createArray('h', 'e', 'l', 'l', 'o')` | ["h", "e", "l", "l", "o"] |  |
| `dataUri` | refused |  |  |  |
| `dataUriToBinary` | refused |  |  |  |
| `dataUriToString` | refused |  |  |  |
| `decodeBase64` | refused |  |  |  |
| `decodeDataUri` | refused |  |  |  |
| `decodeUriComponent` | refused |  |  |  |
| `encodeUriComponent` | refused |  |  |  |
| `float` | runs | `float('10.333')` | 10.333 |  |
| `int` | runs | `int('10')` | 10 |  |
| `json` | runs | `json('[1, 2, 3]')` | [1, 2, 3] |  |
| `string` | runs | `string(10)` | "10" |  |
| `uriComponent` | refused |  |  |  |
| `uriComponentToBinary` | refused |  |  |  |
| `uriComponentToString` | refused |  |  |  |
| `xml` | refused |  |  |  |
| `xpath` | refused |  |  |  |

### Math

| Function | Status | Example | Reference value | Value here |
| --- | --- | --- | --- | --- |
| `add` | runs | `add(1, 1.5)` | 2.5 |  |
| `div` | runs | `div(11, 5)` | 2 |  |
| `max` | runs | `max(1, 2, 3)` | 3 |  |
| `min` | runs | `min(1, 2, 3)` | 1 |  |
| `mod` | runs | `mod(3, 2)` | 1 |  |
| `mul` | runs | `mul(1, 2)` | 2 |  |
| `rand` | refused |  |  |  |
| `range` | runs | `range(1, 4)` | [1, 2, 3, 4] |  |
| `sub` | fails | `sub(10.3, .3)` | 10 | "error: unexpected '.'" |

### Date

| Function | Status | Example | Reference value | Value here |
| --- | --- | --- | --- | --- |
| `addDays` | runs | `addDays('2018-03-15T00:00:00Z', 10)` | "2018-03-25T00:00:00.0000000Z" |  |
| `addHours` | runs | `addHours('2018-03-15T00:00:00Z', 10)` | "2018-03-15T10:00:00.0000000Z" |  |
| `addMinutes` | runs | `addMinutes('2018-03-15T00:10:00Z', 10)` | "2018-03-15T00:20:00.0000000Z" |  |
| `addSeconds` | runs | `addSeconds('2018-03-15T00:00:00Z', 10)` | "2018-03-15T00:00:10.0000000Z" |  |
| `addToTime` | refused |  |  |  |
| `convertFromUtc` | refused |  |  |  |
| `convertTimeZone` | refused |  |  |  |
| `convertToUtc` | refused |  |  |  |
| `dayOfMonth` | refused |  |  |  |
| `dayOfWeek` | refused |  |  |  |
| `dayOfYear` | refused |  |  |  |
| `formatDateTime` | fails | `formatDateTime('03/15/2018 12:00:00', 'yyyy-MM-ddTHH:mm:ss')` | "2018-03-15T12:00:00" | "error: '03/15/2018 12:00:00' isn't a date and time this PC can read (use ISO 8601)" |
| `getFutureTime` | refused |  |  |  |
| `getPastTime` | refused |  |  |  |
| `startOfDay` | runs | `startOfDay('2018-03-15T13:30:30Z')` | "2018-03-15T00:00:00.0000000Z" |  |
| `startOfHour` | refused |  |  |  |
| `startOfMonth` | refused |  |  |  |
| `subtractFromTime` | refused |  |  |  |
| `ticks` | refused |  |  |  |
| `utcNow` | not checked |  |  |  |

### Scope

| Function | Status | Example | Reference value | Value here |
| --- | --- | --- | --- | --- |
| `activity` | not checked |  |  |  |
| `item` | not checked |  |  |  |
| `pipeline` | not checked |  |  |  |
| `variables` | not checked |  |  |  |
