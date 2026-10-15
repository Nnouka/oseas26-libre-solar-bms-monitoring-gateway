<?php

namespace Tests\Feature;

use App\Plugins\LibreSolarBms\Models\LibreSolarBmsDevice;
use App\Plugins\LibreSolarBms\Services\LibreSolarBmsService;
use Database\Factories\UserFactory;
use Tests\RefreshMultipleDatabases;
use Tests\TestCase;

class LibreSolarBmsReadApiTest extends TestCase {
    use RefreshMultipleDatabases;

    private const BASE = '/api/libre-solar-bms/batteries';

    private int $batteryId = 0;

    private function seedHistory(): void {
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
        $this->batteryId = LibreSolarBmsDevice::query()->where('device_id', 'bms-1')->firstOrFail()->id;
    }

    private function api(string $suffix, ?int $batteryId = null) {
        $user = UserFactory::new()->create();
        $this->assignRole($user, 'admin');

        return $this->actingAs($user)->getJson(self::BASE.'/'.($batteryId ?? $this->batteryId).$suffix);
    }

    /**
     * @return list<float>
     */
    private function floats(array $rows, string $key): array {
        return array_map('floatval', array_column($rows, $key));
    }

    public function testSummariesAreListedNewestFirstAndPaged(): void {
        $this->seedHistory();
        $response = $this->api('/summaries?per_page=3');
        $response->assertOk();
        $this->assertSame([10.0, 40.0, 62.0], $this->floats($response->json('data'), 'soc_pct'));
        $this->assertSame(4, $response->json('total'));
        $this->assertSame(2, $response->json('last_page'));
        $this->assertSame([60.0], $this->floats($this->api('/summaries?per_page=3&page=2')->json('data'), 'soc_pct'));
    }

    public function testSummariesCanBeFilteredByTimeRangeAndHealth(): void {
        $this->seedHistory();
        $this->assertCount(2, $this->api('/summaries?from=2026-10-15T11:00:00Z')->json('data'));
        $this->assertCount(2, $this->api('/summaries?to=2026-10-15T10:30:00Z')->json('data'));
        $this->assertCount(1, $this->api('/summaries?health=red')->json('data'));
        $this->assertCount(0, $this->api('/summaries?from=2027-01-01')->json('data'));
    }

    public function testBadFiltersAreRejectedAndUnknownBatteriesAre404(): void {
        $this->seedHistory();
        $this->api('/summaries?health=purple')->assertStatus(422);
        $this->api('/summaries?per_page=0')->assertStatus(422);
        $this->api('/summaries?per_page=100000')->assertStatus(422);
        $this->api('/summaries?from=2026-10-16&to=2026-10-15')->assertStatus(422);
        $this->api('/summaries?from=not-a-date')->assertStatus(422);
        $this->api('/summaries', $this->batteryId + 1000)->assertStatus(404);
    }

    public function testTrendAveragesPerBucketOldestFirstAndSkipsEmptyBuckets(): void {
        $this->seedHistory();
        $response = $this->api('/trend?from=2026-10-15T10:00:00Z&to=2026-10-15T12:00:00Z&buckets=4');
        $response->assertOk();
        $points = $response->json('data');
        // 30-minute buckets: 10:00 (two summaries), 10:30 empty, 11:00, 11:30 empty, 12:00 (the "to" instant)
        $this->assertCount(3, $points);
        $this->assertEqualsWithDelta(61.0, $points[0]['soc_pct'], 0.1);
        $this->assertEqualsWithDelta(40.0, $points[1]['soc_pct'], 0.01);
        $this->assertGreaterThan($points[1]['t'], $points[2]['t']);
        $this->assertGreaterThan($points[0]['t'], $points[1]['t']);
    }

    public function testTrendWithNoDataIsEmptyAndWithOnePointIsOnePoint(): void {
        $this->seedHistory();
        $this->assertSame([], $this->api('/trend?from=2027-01-01&to=2027-01-02')->json('data'));
        $this->assertCount(1, $this->api('/trend?from=2026-10-15T11:59:00Z&to=2026-10-15T12:01:00Z')->json('data'));
        $this->api('/trend?buckets=0')->assertStatus(422);
        $this->api('/trend?buckets=301')->assertStatus(422);
    }

    public function testEventsFiltersAndTheOldLimitParameterStillWork(): void {
        $this->seedHistory();
        $this->assertCount(3, $this->api('/events')->json('data'));
        $this->assertCount(2, $this->api('/events?limit=2')->json('data'));
        $this->assertCount(1, $this->api('/events?severity=critical')->json('data'));
        $this->assertCount(2, $this->api('/events?type=fault_raised')->json('data'));
        $this->assertCount(1, $this->api('/events?from=2026-10-15T11:00:00Z')->json('data'));
        $this->api('/events?severity=loud')->assertStatus(422);
        $this->assertSame('LOW_SOC', $this->api('/events')->json('data.0.fault'));       // newest first
    }
}
