# Battery Health History Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Keep every summary MPM receives, and let an operator review summaries and events of a battery by time, with trend charts, on the Battery Health detail page. Plugin-only: no bridge or ingestor change.

**Architecture:** A new tenant table `libre_solar_bms_summaries` (one row per summary, idempotent by a hash of device and timestamp). `LibreSolarBmsService::recordSummary` writes the history row before its existing "stale summary" check. Three read endpoints (paged summaries, bucketed trend, filtered paged events). The detail page becomes three tabs (Overview with trend charts, History, Events) built from small Vue components; charts are inline SVG.

**Tech Stack:** Laravel 12 / PHPUnit (MPM plugin), MySQL 8, Vue 2 + Material (MPM frontend), inline SVG.

**Spec:** `docs/superpowers/specs/2026-10-15-high-priority-todos-design.md` (section "Item 3").

## Global Constraints

- Plugin-only: files under `integrations/micropowermanager/` (backend and frontend overlay). Do not edit `apply.py`'s patching of MPM core files; new plugin files are copied by its `copytree`.
- The existing migration `2026_10_13_000000_create_libre_solar_bms_tables.php` is not edited; add a new tenant migration.
- Existing API stays compatible: `GET /api/libre-solar-bms/batteries`, `/batteries/{id}` unchanged; `/batteries/{id}/events` keeps the `limit` parameter and its `data` array.
- Only fields already in a summary are shown (state, SoC, SoH, cycles, pack voltage, imbalance, max temp, health, score, reasons, active faults). No per-cell data. No retention pruning. No CSV export.
- Read endpoints stay under `auth:api` only (as today).
- Commit messages end with: `Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>`.
- **Running PHP tests** (MPM refuses to run them against the dev database; use the dedicated testing DB). From `C:\hackathons\en-access\micropowermanager` after `python <this repo>/integrations/micropowermanager/apply.py .`:
  ```bash
  docker compose up -d redis mysql_testing mysql
  PW=$(grep MYSQL_ROOT_PASSWORD dev/.env.mysql_testing | cut -d= -f2-)
  docker compose run --rm --no-deps --entrypoint "" -e APP_ENV=testing -e DB_HOST=mysql_testing -e DB_DATABASE=mpm_testing -e DB_PASSWORD="$PW" backend-dev sh -c "cd /var/www/html && php artisan test --filter=LibreSolarBms"
  ```
  The first test is slow (about 3 minutes: it migrates the tenant schema), later ones about 10 s each. Stop the `mpm` profile stack of this repo's compose first only if ports clash (this command publishes no ports).

## Review Focus

1. A late (older) summary replayed after an outage: stored in history, does NOT overwrite the current device row. (Task 1)
2. The same summary delivered twice: one history row. Two different devices with the same timestamp: two rows. (Task 1)
3. Empty ranges and unknown filters: `from` after `to`, a `health` value that is not green/amber/red, `per_page` of 0 or 100000, a battery id that does not exist (404), timestamps without a timezone. (Task 2)
4. Trend over a range with no data, with a single point, and with gaps: empty `data` array, one bucket, only non-empty buckets. (Task 2)
5. Batteries with `null` fields (not every summary has every number): charts skip nulls, tables show "-". (Tasks 4-5)
6. A battery that reports every 30 s for 24 h (2880 rows): the trend stays one small response (bucketed) and the History tab pages. (Tasks 2, 5)

---

### Task 1: History table, model, and recording

**Files:**
- Create: `integrations/micropowermanager/backend/database/migrations/tenant/2026_10_15_000000_create_libre_solar_bms_summaries_table.php`
- Create: `integrations/micropowermanager/backend/app/Plugins/LibreSolarBms/Models/LibreSolarBmsSummary.php`
- Modify: `integrations/micropowermanager/backend/app/Plugins/LibreSolarBms/Models/LibreSolarBmsDevice.php` (relation, docblock)
- Modify: `integrations/micropowermanager/backend/app/Plugins/LibreSolarBms/Services/LibreSolarBmsService.php` (`recordSummary`)
- Create: `integrations/micropowermanager/backend/tests/Feature/LibreSolarBmsHistoryTest.php`

**Interfaces:**
- Produces: table `libre_solar_bms_summaries`; model `LibreSolarBmsSummary` (fields below); `LibreSolarBmsDevice::summaries(): HasMany`; `LibreSolarBmsService::recordSummary(array $summary): LibreSolarBmsDevice` keeps its signature and now also stores history.

- [ ] **Step 1: Write the failing tests**

`LibreSolarBmsHistoryTest.php`:
```php
<?php

namespace Tests\Feature;

use App\Plugins\LibreSolarBms\Models\LibreSolarBmsSummary;
use App\Plugins\LibreSolarBms\Services\LibreSolarBmsService;
use Tests\RefreshMultipleDatabases;
use Tests\TestCase;

class LibreSolarBmsHistoryTest extends TestCase {
    use RefreshMultipleDatabases;

    private function summary(string $ts, array $extra = []): array {
        return array_merge([
            'device_id' => 'bms-1', 'ts' => $ts, 'state' => 'NORMAL', 'soc_pct' => 70.0, 'soh_pct' => 99.0,
            'cycles' => 1.5, 'pack_voltage_v' => 26.4, 'imbalance_v' => 0.02, 'max_temp_c' => 30.0,
            'health' => 'green', 'health_score' => 100, 'health_reasons' => [], 'active_faults' => [],
        ], $extra);
    }

    private function service(): LibreSolarBmsService {
        return $this->app->make(LibreSolarBmsService::class);
    }

    public function testEverySummaryIsKeptInHistory(): void {
        $service = $this->service();
        $service->recordSummary($this->summary('2026-10-15T10:00:00Z'));
        $service->recordSummary($this->summary('2026-10-15T10:15:00Z', ['soc_pct' => 71.0]));

        $this->assertSame(2, LibreSolarBmsSummary::query()->count());
        $this->assertSame('71.0', (string) $service->getBattery(1)->soc_pct);
    }

    public function testTheSameSummaryDeliveredTwiceIsStoredOnce(): void {
        $service = $this->service();
        $service->recordSummary($this->summary('2026-10-15T10:00:00Z'));
        $service->recordSummary($this->summary('2026-10-15T10:00:00Z'));

        $this->assertSame(1, LibreSolarBmsSummary::query()->count());
    }

    public function testTwoDevicesWithTheSameTimestampAreTwoRows(): void {
        $service = $this->service();
        $service->recordSummary($this->summary('2026-10-15T10:00:00Z'));
        $service->recordSummary($this->summary('2026-10-15T10:00:00Z', ['device_id' => 'bms-2']));

        $this->assertSame(2, LibreSolarBmsSummary::query()->count());
    }

    public function testALateSummaryIsKeptInHistoryButDoesNotOverwriteTheCurrentState(): void {
        $service = $this->service();
        $service->recordSummary($this->summary('2026-10-15T10:15:00Z', ['soc_pct' => 71.0]));
        $service->recordSummary($this->summary('2026-10-15T10:00:00Z', ['soc_pct' => 50.0]));

        $this->assertSame(2, LibreSolarBmsSummary::query()->count());
        $this->assertSame('71.0', (string) $service->getBattery(1)->soc_pct);
    }

    public function testNullFieldsAndReasonsAreStored(): void {
        $this->service()->recordSummary(
            $this->summary('2026-10-15T10:00:00Z', ['soc_pct' => null, 'health' => 'amber', 'health_reasons' => ['Low charge'], 'active_faults' => ['CELL_UNDERVOLTAGE']])
        );

        $row = LibreSolarBmsSummary::query()->firstOrFail();
        $this->assertNull($row->soc_pct);
        $this->assertSame(['Low charge'], $row->health_reasons);
        $this->assertSame(['CELL_UNDERVOLTAGE'], $row->active_faults);
    }
}
```

- [ ] **Step 2: Run to verify they fail**

Run the PHP test command from Global Constraints (after `apply.py`) with `--filter=LibreSolarBmsHistoryTest`.
Expected: FAIL (class `LibreSolarBmsSummary` not found).

- [ ] **Step 3: Implement**

Migration:
```php
<?php

use Illuminate\Database\Migrations\Migration;
use Illuminate\Database\Schema\Blueprint;
use Illuminate\Support\Facades\Schema;

return new class extends Migration {
    public function up(): void {
        Schema::connection('tenant')->create('libre_solar_bms_summaries', function (Blueprint $table) {
            $table->id();
            $table->unsignedBigInteger('libre_solar_bms_device_id');
            $table->string('summary_key', 40)->unique();
            $table->timestamp('reported_at');
            $table->string('state')->nullable();
            $table->decimal('soc_pct', 5, 1)->nullable();
            $table->decimal('soh_pct', 5, 1)->nullable();
            $table->decimal('cycles', 10, 2)->nullable();
            $table->decimal('pack_voltage_v', 7, 2)->nullable();
            $table->decimal('imbalance_v', 6, 3)->nullable();
            $table->decimal('max_temp_c', 5, 1)->nullable();
            $table->string('health');
            $table->unsignedTinyInteger('health_score')->nullable();
            $table->json('health_reasons')->nullable();
            $table->json('active_faults')->nullable();
            $table->timestamps();

            $table->index(['libre_solar_bms_device_id', 'reported_at'], 'lsb_summaries_device_time');
        });
    }

    public function down(): void {
        Schema::connection('tenant')->dropIfExists('libre_solar_bms_summaries');
    }
};
```
Model `LibreSolarBmsSummary` (same style as `LibreSolarBmsEvent`): `$table = 'libre_solar_bms_summaries'`, casts `reported_at` datetime, `health_reasons` and `active_faults` array, docblock with all properties, `device(): BelongsTo`.

`LibreSolarBmsDevice`: add `summaries(): HasMany` (foreign key `libre_solar_bms_device_id`) and change the class docblock sentence "Only the most recent summary is kept ..." to "The latest summary is kept here; every summary is also stored in libre_solar_bms_summaries."

`LibreSolarBmsService`: inject `LibreSolarBmsSummary $summaryModel` in the constructor and rewrite the start of `recordSummary`:
```php
        $battery = $this->findOrRegister($summary['device_id']);
        $reportedAt = Carbon::parse($summary['ts']);

        // Every summary goes to history first (idempotent), whatever its age.
        $this->summaryModel->newQuery()->firstOrCreate(
            ['summary_key' => sha1($summary['device_id'].'|'.$summary['ts'])],
            [
                'libre_solar_bms_device_id' => $battery->id,
                'reported_at' => $reportedAt,
                'state' => $summary['state'] ?? null,
                'soc_pct' => $summary['soc_pct'] ?? null,
                'soh_pct' => $summary['soh_pct'] ?? null,
                'cycles' => $summary['cycles'] ?? null,
                'pack_voltage_v' => $summary['pack_voltage_v'] ?? null,
                'imbalance_v' => $summary['imbalance_v'] ?? null,
                'max_temp_c' => $summary['max_temp_c'] ?? null,
                'health' => $summary['health'],
                'health_score' => $summary['health_score'] ?? null,
                'health_reasons' => $summary['health_reasons'] ?? [],
                'active_faults' => $summary['active_faults'] ?? [],
            ],
        );
```
followed by the existing stale check and `fill` code unchanged. (Note: the key uses the raw `ts` string, so the same timestamp spelled differently is a different row; the ingestor republishes the same string, which is the case that matters.)

- [ ] **Step 4: Run to verify they pass**

Same command. Expected: `5 passed`.

- [ ] **Step 5: Commit**

```bash
git add integrations/micropowermanager/backend
git commit -m "feat(mpm-plugin): keep every summary in a history table" -m "Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
```

---

### Task 2: Read endpoints (summaries, trend, filtered events)

**Files:**
- Modify: `integrations/micropowermanager/backend/app/Plugins/LibreSolarBms/Services/LibreSolarBmsService.php`
- Modify: `integrations/micropowermanager/backend/app/Plugins/LibreSolarBms/Http/Controllers/LibreSolarBmsBatteryController.php`
- Modify: `integrations/micropowermanager/backend/app/Plugins/LibreSolarBms/routes/api.php`
- Create: `integrations/micropowermanager/backend/app/Plugins/LibreSolarBms/Http/Requests/HistoryQueryRequest.php`
- Create: `integrations/micropowermanager/backend/tests/Feature/LibreSolarBmsReadApiTest.php`

**Interfaces:**
- Consumes (Task 1): `LibreSolarBmsSummary`, `recordSummary`.
- Produces:
  - `GET /api/libre-solar-bms/batteries/{id}/summaries?from=&to=&health=&page=&per_page=` -> paginated `data[]` of summary rows (`reported_at`, `state`, `soc_pct`, `soh_pct`, `cycles`, `pack_voltage_v`, `imbalance_v`, `max_temp_c`, `health`, `health_score`, `health_reasons`, `active_faults`), newest first. `per_page` default 25, max 200.
  - `GET /api/libre-solar-bms/batteries/{id}/trend?from=&to=&buckets=` -> `{"data":[{"t":"ISO-8601","soc_pct":..,"pack_voltage_v":..,"max_temp_c":..,"imbalance_v":..}, ...]}` oldest first, one point per non-empty bucket (average of non-null values; a metric with only nulls in a bucket is `null`). `buckets` default 96, min 1, max 300. `from` defaults to 24 h before `to`; `to` defaults to now.
  - `GET /api/libre-solar-bms/batteries/{id}/events?from=&to=&severity=&type=&page=&per_page=&limit=` -> paginated `data[]` (same item shape as today), newest first; `per_page` default is `limit` (default 50), max 200. `severity` in `info|warning|critical`.
  - Validation (HistoryQueryRequest): `from`/`to` valid dates, `from` <= `to`, `health` in `green|amber|red`, `severity` in `info|warning|critical`, `type` string max 50, `page` >= 1, `per_page` 1..200, `buckets` 1..300. Violations -> HTTP 422. Unknown battery -> 404.

- [ ] **Step 1: Write the failing tests** (`LibreSolarBmsReadApiTest.php`)

```php
<?php

namespace Tests\Feature;

use Database\Factories\UserFactory;
use App\Plugins\LibreSolarBms\Services\LibreSolarBmsService;
use Tests\RefreshMultipleDatabases;
use Tests\TestCase;

class LibreSolarBmsReadApiTest extends TestCase {
    use RefreshMultipleDatabases;

    private const BASE = '/api/libre-solar-bms/batteries';

    private function seed(): void {
        $service = $this->app->make(LibreSolarBmsService::class);
        foreach ([
            ['2026-10-15T10:00:00Z', 60.0, 'green'],
            ['2026-10-15T10:10:00Z', 62.0, 'green'],
            ['2026-10-15T11:00:00Z', 40.0, 'amber'],
            ['2026-10-15T12:00:00Z', 10.0, 'red'],
        ] as [$ts, $soc, $health]) {
            $service->recordSummary([
                'device_id' => 'bms-1', 'ts' => $ts, 'soc_pct' => $soc, 'health' => $health,
                'pack_voltage_v' => 26.0, 'max_temp_c' => 30.0, 'imbalance_v' => 0.02,
            ]);
        }
        $service->recordEvents([
            ['device_id' => 'bms-1', 'ts' => '2026-10-15T10:30:00Z', 'type' => 'fault_raised', 'fault' => 'CHG_OVERTEMP', 'severity' => 'critical', 'message' => 'hot', 'action' => 'cool it'],
            ['device_id' => 'bms-1', 'ts' => '2026-10-15T10:45:00Z', 'type' => 'fault_cleared', 'fault' => 'CHG_OVERTEMP', 'severity' => 'info', 'message' => 'ok'],
            ['device_id' => 'bms-1', 'ts' => '2026-10-15T11:30:00Z', 'type' => 'fault_raised', 'fault' => 'LOW_SOC', 'severity' => 'warning', 'message' => 'low'],
        ]);
    }

    private function get(string $path) {
        $user = UserFactory::new()->create();
        $this->assignRole($user, 'admin');

        return $this->actingAs($user)->getJson(self::BASE.$path);
    }

    public function testSummariesAreListedNewestFirstAndPaged(): void {
        $this->seed();
        $response = $this->get('/1/summaries?per_page=3');
        $response->assertOk();
        $this->assertSame([10.0, 40.0, 62.0], array_map('floatval', array_column($response->json('data'), 'soc_pct')));
        $this->assertSame(4, $response->json('meta.total'));
        $this->assertSame(['10.0'], array_column($this->get('/1/summaries?per_page=3&page=2')->json('data'), 'soc_pct'));
    }

    public function testSummariesCanBeFilteredByTimeRangeAndHealth(): void {
        $this->seed();
        $this->assertCount(2, $this->get('/1/summaries?from=2026-10-15T10:30:00Z')->json('data'));
        $this->assertCount(2, $this->get('/1/summaries?to=2026-10-15T10:30:00Z')->json('data'));
        $this->assertCount(1, $this->get('/1/summaries?health=red')->json('data'));
        $this->assertCount(0, $this->get('/1/summaries?from=2027-01-01')->json('data'));
    }

    public function testBadFiltersAreRejectedAndUnknownBatteriesAre404(): void {
        $this->seed();
        $this->get('/1/summaries?health=purple')->assertStatus(422);
        $this->get('/1/summaries?per_page=0')->assertStatus(422);
        $this->get('/1/summaries?per_page=100000')->assertStatus(422);
        $this->get('/1/summaries?from=2026-10-16&to=2026-10-15')->assertStatus(422);
        $this->get('/1/summaries?from=not-a-date')->assertStatus(422);
        $this->get('/999/summaries')->assertStatus(404);
    }

    public function testTrendAveragesPerBucketOldestFirstAndSkipsEmptyBuckets(): void {
        $this->seed();
        $response = $this->get('/1/trend?from=2026-10-15T10:00:00Z&to=2026-10-15T12:00:00Z&buckets=4');
        $response->assertOk();
        $points = $response->json('data');
        $this->assertCount(3, $points);                         // 10:00-10:30 (two summaries), 11:00, 12:00; 11:30 bucket empty
        $this->assertEqualsWithDelta(61.0, $points[0]['soc_pct'], 0.1);
        $this->assertLessThan($points[1]['t'], $points[2]['t']);
    }

    public function testTrendWithNoDataIsEmptyAndWithOnePointIsOnePoint(): void {
        $this->seed();
        $this->assertSame([], $this->get('/1/trend?from=2027-01-01&to=2027-01-02')->json('data'));
        $this->assertCount(1, $this->get('/1/trend?from=2026-10-15T11:59:00Z&to=2026-10-15T12:01:00Z')->json('data'));
        $this->get('/1/trend?buckets=0')->assertStatus(422);
        $this->get('/1/trend?buckets=301')->assertStatus(422);
    }

    public function testEventsFiltersAndTheOldLimitParameterStillWork(): void {
        $this->seed();
        $this->assertCount(3, $this->get('/1/events')->json('data'));
        $this->assertCount(2, $this->get('/1/events?limit=2')->json('data'));
        $this->assertCount(1, $this->get('/1/events?severity=critical')->json('data'));
        $this->assertCount(2, $this->get('/1/events?type=fault_raised')->json('data'));
        $this->assertCount(1, $this->get('/1/events?from=2026-10-15T11:00:00Z')->json('data'));
        $this->get('/1/events?severity=loud')->assertStatus(422);
        $this->assertSame('LOW_SOC', $this->get('/1/events')->json('data.0.fault'));       // newest first
    }
}
```

- [ ] **Step 2: Run to verify they fail** (`--filter=LibreSolarBmsReadApiTest`). Expected: FAIL (404 for `/summaries`, `/trend`).

- [ ] **Step 3: Implement**

`HistoryQueryRequest` (authorize true):
```php
    public function rules(): array {
        return [
            'from' => ['nullable', 'date'],
            'to' => ['nullable', 'date', 'after_or_equal:from'],
            'health' => ['nullable', 'in:green,amber,red'],
            'severity' => ['nullable', 'in:info,warning,critical'],
            'type' => ['nullable', 'string', 'max:50'],
            'page' => ['nullable', 'integer', 'min:1'],
            'per_page' => ['nullable', 'integer', 'between:1,200'],
            'limit' => ['nullable', 'integer', 'min:1'],
            'buckets' => ['nullable', 'integer', 'between:1,300'],
        ];
    }
```
Service (add methods; `use Illuminate\Contracts\Pagination\LengthAwarePaginator; use Illuminate\Support\Facades\DB;`):
```php
    /** @param array<string, mixed> $filters from, to, health */
    public function summaries(int $batteryId, array $filters, int $perPage): LengthAwarePaginator {
        $this->getBattery($batteryId);

        return $this->summaryModel->newQuery()
            ->where('libre_solar_bms_device_id', $batteryId)
            ->when($filters['from'] ?? null, fn ($q, $v) => $q->where('reported_at', '>=', Carbon::parse($v)))
            ->when($filters['to'] ?? null, fn ($q, $v) => $q->where('reported_at', '<=', Carbon::parse($v)))
            ->when($filters['health'] ?? null, fn ($q, $v) => $q->where('health', $v))
            ->orderByDesc('reported_at')->orderByDesc('id')
            ->paginate($perPage);
    }

    /** @return list<array<string, mixed>> */
    public function trend(int $batteryId, Carbon $from, Carbon $to, int $buckets): array {
        $this->getBattery($batteryId);
        $span = max(1, $to->diffInSeconds($from, true));
        $width = max(1, (int) ceil($span / $buckets));

        $rows = $this->summaryModel->newQuery()
            ->where('libre_solar_bms_device_id', $batteryId)
            ->whereBetween('reported_at', [$from, $to])
            ->selectRaw('FLOOR((UNIX_TIMESTAMP(reported_at) - ?) / ?) AS bucket', [$from->getTimestamp(), $width])
            ->selectRaw('AVG(soc_pct) AS soc_pct, AVG(pack_voltage_v) AS pack_voltage_v, AVG(max_temp_c) AS max_temp_c, AVG(imbalance_v) AS imbalance_v')
            ->groupBy('bucket')->orderBy('bucket')->get();

        return $rows->map(fn ($r) => [
            't' => Carbon::createFromTimestamp($from->getTimestamp() + (int) $r->bucket * $width)->toIso8601String(),
            'soc_pct' => $r->soc_pct === null ? null : round((float) $r->soc_pct, 1),
            'pack_voltage_v' => $r->pack_voltage_v === null ? null : round((float) $r->pack_voltage_v, 2),
            'max_temp_c' => $r->max_temp_c === null ? null : round((float) $r->max_temp_c, 1),
            'imbalance_v' => $r->imbalance_v === null ? null : round((float) $r->imbalance_v, 3),
        ])->all();
    }

    /** @param array<string, mixed> $filters from, to, severity, type */
    public function events(int $batteryId, array $filters, int $perPage): LengthAwarePaginator {
        $this->getBattery($batteryId);

        return $this->event->newQuery()
            ->where('libre_solar_bms_device_id', $batteryId)
            ->when($filters['from'] ?? null, fn ($q, $v) => $q->where('occurred_at', '>=', Carbon::parse($v)))
            ->when($filters['to'] ?? null, fn ($q, $v) => $q->where('occurred_at', '<=', Carbon::parse($v)))
            ->when($filters['severity'] ?? null, fn ($q, $v) => $q->where('severity', $v))
            ->when($filters['type'] ?? null, fn ($q, $v) => $q->where('type', $v))
            ->orderByDesc('occurred_at')->orderByDesc('id')
            ->paginate($perPage);
    }
```
(`getBattery` uses `findOrFail`, giving 404.) Keep `latestEvents` (unused callers may exist; remove it only if nothing else references it).

Controller: replace `events` and add the others:
```php
    public function summaries(HistoryQueryRequest $request, int $batteryId): ApiResource {
        return ApiResource::make($this->service->summaries($batteryId, $request->validated(), $request->integer('per_page', 25)));
    }

    public function trend(HistoryQueryRequest $request, int $batteryId): ApiResource {
        $to = $request->filled('to') ? Carbon::parse($request->input('to')) : Carbon::now();
        $from = $request->filled('from') ? Carbon::parse($request->input('from')) : $to->copy()->subDay();

        return ApiResource::make($this->service->trend($batteryId, $from, $to, $request->integer('buckets', 96)));
    }

    public function events(HistoryQueryRequest $request, int $batteryId): ApiResource {
        $perPage = $request->integer('per_page', min($request->integer('limit', self::EVENT_LIMIT_DEFAULT), self::EVENT_LIMIT_MAX));

        return ApiResource::make($this->service->events($batteryId, $request->validated(), max($perPage, 1)));
    }
```
(`use App\Plugins\LibreSolarBms\Http\Requests\HistoryQueryRequest; use Illuminate\Support\Carbon;`; drop the now-unused `Illuminate\Http\Request` import.)
Routes: add inside the `auth:api` group:
```php
        Route::get('/batteries/{batteryId}/summaries', [LibreSolarBmsBatteryController::class, 'summaries'])->where('batteryId', '[0-9]+');
        Route::get('/batteries/{batteryId}/trend', [LibreSolarBmsBatteryController::class, 'trend'])->where('batteryId', '[0-9]+');
```
Note: `ApiResource::make(array)` for the trend wraps the list in `data`.

- [ ] **Step 4: Run both test classes.** Expected: all pass (`LibreSolarBms` filter). Fix real failures by debugging; if the bucket arithmetic disagrees with the test's expectation, re-derive the expectation by hand from the seeded timestamps before changing either.

- [ ] **Step 5: Commit**

```bash
git add integrations/micropowermanager/backend
git commit -m "feat(mpm-plugin): paged summaries, bucketed trend and filtered events endpoints" -m "Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
```

---

### Task 3: Frontend data layer and shared components

**Files:**
- Modify: `integrations/micropowermanager/frontend/src/plugins/libre-solar-bms/repositories/BatteryRepository.js`
- Create: `integrations/micropowermanager/frontend/src/plugins/libre-solar-bms/shared/TrendChart.vue`
- Create: `integrations/micropowermanager/frontend/src/plugins/libre-solar-bms/shared/TimeRange.vue`
- Create: `integrations/micropowermanager/frontend/src/plugins/libre-solar-bms/shared/format.js`

**Interfaces:**
- Produces:
  - `BatteryRepository.summaries(id, params)`, `.trend(id, params)`, `.events(id, params)` (params object passed as query; `events` keeps working with the old numeric `limit` argument: `events(id, limit)` where a number is treated as `{limit}`).
  - `format.js`: `fmtNumber(v, digits, unit="")` (`"-"` for null/undefined/NaN), `fmtTime(iso)` (locale string or `"-"`).
  - `<time-range v-model="range">`: `range` is `{ preset: "24h"|"7d"|"30d"|"custom", from: string|null, to: string|null }` (ISO strings, `null` = open); emits `input` on change. Presets set `from` to now minus the span and `to` to `null`.
  - `<trend-chart :points="[{t, v}]" :unit="'%'" :title="'State of charge'" :color="'#2e7d32'" :min :max>`: renders an inline SVG line chart (`viewBox` 0 0 400 120), skipping points whose `v` is `null` (a gap), with first/last time labels and min/max value labels; shows "No data in this range" when fewer than one valid point; a single valid point renders as a dot.

- [ ] **Step 1: Implement** the repository additions:
```js
export default {
  list() { return Client.get(resource) },
  get(batteryId) { return Client.get(`${resource}/${batteryId}`) },
  summaries(batteryId, params = {}) { return Client.get(`${resource}/${batteryId}/summaries`, { params }) },
  trend(batteryId, params = {}) { return Client.get(`${resource}/${batteryId}/trend`, { params }) },
  events(batteryId, params = {}) {
    const query = typeof params === "number" ? { limit: params } : params
    return Client.get(`${resource}/${batteryId}/events`, { params: query })
  },
}
```
`format.js`, `TimeRange.vue` (a `md-field`/button group for presets plus two `datetime-local` inputs when `custom`), `TrendChart.vue` (SVG polyline built from a computed list of segments split at null values; x scaled by time, y scaled between min/max with padding; `role="img"` and an `aria-label` made of title plus last value).

- [ ] **Step 2: Build check**

Rebuild the frontend image: `docker compose --profile mpm up --build -d mpm-frontend` (repo root). Expected: build succeeds (the Vue compiler catches syntax and template errors).

- [ ] **Step 3: Commit**

```bash
git add integrations/micropowermanager/frontend
git commit -m "feat(mpm-plugin): repository methods and chart/time-range components" -m "Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
```

---

### Task 4: Detail page tabs (Overview with trends, History, Events)

**Files:**
- Modify: `integrations/micropowermanager/frontend/src/plugins/libre-solar-bms/modules/Battery/BatteryDetail.vue`
- Create: `integrations/micropowermanager/frontend/src/plugins/libre-solar-bms/modules/Battery/OverviewTab.vue`
- Create: `integrations/micropowermanager/frontend/src/plugins/libre-solar-bms/modules/Battery/HistoryTab.vue`
- Create: `integrations/micropowermanager/frontend/src/plugins/libre-solar-bms/modules/Battery/EventsTab.vue`

**Interfaces:**
- Consumes (Task 3): repository methods, `TimeRange`, `TrendChart`, `format.js`; (Task 2) the endpoints.
- Produces: `BatteryDetail` keeps loading the battery (30 s poll) and shows the banner, then three tabs (buttons with `role="tab"`, `aria-selected`) rendering `OverviewTab`, `HistoryTab`, `EventsTab`, each with props `batteryId` (and `battery` for Overview). The route and `apply.py` patches do not change.

- [ ] **Step 1: Implement**
  - `OverviewTab`: the existing tiles; active faults as chips (`(battery.active_faults || [])`, "None" when empty); "Last report: <time> (x min ago)"; `<time-range>` default `24h`; four `<trend-chart>`s (State of charge %, Pack voltage V, Max temperature °C, Cell imbalance mV) fed by `trend(batteryId, {from, to, buckets: 96})`; reloads when the range changes and every 30 s.
  - `HistoryTab`: `<time-range>`, a health filter select (All, Healthy, Needs attention, Critical), table (Time, Health badge, Score, State, SoC, SoH, Cycles, Voltage, Max temp, Imbalance), row click toggles a detail row listing reasons and active faults, previous/next paging with "Page x of y (n summaries)", empty state "No summaries in this range".
  - `EventsTab`: `<time-range>`, severity select (All, info, warning, critical), type select (All, fault_raised, fault_cleared), the existing table (Time, Severity, Event, Recommended action), paging, empty state "No events in this range".
  - All three: request errors show "Could not load ..." in a `p.error`; changing any filter resets to page 1; stale responses are ignored (keep a request counter and drop out-of-order replies).

- [ ] **Step 2: Build check** as in Task 3 Step 2.

- [ ] **Step 3: Commit**

```bash
git add integrations/micropowermanager/frontend
git commit -m "feat(mpm-plugin): battery detail tabs with trends, history and filtered events" -m "Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
```

---

### Task 5: End-to-end check on the running stack, docs

**Files:**
- Modify: `integrations/micropowermanager/README.md`, `TODO.txt`

- [ ] **Step 1: Rebuild and migrate**: `docker compose --profile mpm up --build -d` (repo root). The backend entrypoint runs `migrate-tenant`, creating the new table for the existing company. Expected: `docker compose --profile mpm logs mpm-backend | grep -i libre_solar_bms_summaries` shows it created, and `mpm-bootstrap` exits 0 again.
- [ ] **Step 2: Let data accumulate** (a few minutes at the default 30 s summaries), and trigger a fault: `curl -X POST localhost:8010/scenario/overheat`.
- [ ] **Step 3: API check** with an owner token (`POST /api/auth/login`): `/batteries/1/summaries?per_page=5` returns newest first with `meta.total` growing; `/batteries/1/trend?buckets=20` returns up to 20 points; `/batteries/1/events?severity=critical` returns the fault events; `/batteries/1/summaries?health=purple` is 422.
- [ ] **Step 4: UI check** (browser, http://localhost:8001): open Battery Health, Batteries, then the battery. Verify the three tabs, the four charts show lines (and the empty state for a range with no data, for example custom range in the future), History pages and filters by health, Events filters by severity, and that switching the time range or page does not flash stale data. Report what you saw; screenshots are optional.
- [ ] **Step 5: Docs**: in `integrations/micropowermanager/README.md` update "How it works" (history table, the three read endpoints, the tabs) and the "Known limits" (no per-cell data, no retention pruning). Delete item 3 from `TODO.txt`.
- [ ] **Step 6: Commit**

```bash
git add integrations/micropowermanager/README.md TODO.txt
git commit -m "docs: battery health history" -m "Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
```

---

## Self-review

- **Spec coverage:** history table with unique key and `(device, reported_at)` index (Task 1); history written before the stale check (Task 1); summaries endpoint with from/to/health/paging (Task 2); events filters with `limit` still working (Task 2); Overview tiles + fault chips + trends 24h/7d/custom (Tasks 3-4, presets 24h/7d/30d/custom); History and Events tabs with filters and paging (Task 4); tests for idempotent ingest, out-of-order, filters, paging (Tasks 1-2); manual frontend check (Task 5). `apply.py` needs no change: it copies the whole `backend/` and `frontend/` trees.
- **Placeholders:** none; Vue components are specified by behaviour and props (their markup is the implementer's, checked by build and the Task 5 UI check).
- **Type consistency:** `summaries`, `trend`, `events` (repository, controller, service), `HistoryQueryRequest`, `LibreSolarBmsSummary`, `summary_key`, `reported_at` are used identically across tasks.
