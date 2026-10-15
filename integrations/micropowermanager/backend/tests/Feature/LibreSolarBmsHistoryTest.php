<?php

namespace Tests\Feature;

use App\Plugins\LibreSolarBms\Models\LibreSolarBmsDevice;
use App\Plugins\LibreSolarBms\Models\LibreSolarBmsSummary;
use App\Plugins\LibreSolarBms\Services\LibreSolarBmsService;
use Tests\RefreshMultipleDatabases;
use Tests\TestCase;

class LibreSolarBmsHistoryTest extends TestCase {
    use RefreshMultipleDatabases;

    /**
     * @param array<string, mixed> $extra
     *
     * @return array<string, mixed>
     */
    private function summary(string $ts, array $extra = []): array {
        return array_merge([
            'device_id' => 'bms-1', 'ts' => $ts, 'state' => 'NORMAL', 'soc_pct' => 70.0, 'soh_pct' => 99.0,
            'cycles' => 1.5, 'pack_voltage_v' => 26.4, 'imbalance_v' => 0.02, 'max_temp_c' => 30.0,
            'health' => 'green', 'health_score' => 100, 'health_reasons' => [], 'active_faults' => [],
        ], $extra);
    }

    private function current(string $deviceId): LibreSolarBmsDevice {
        return LibreSolarBmsDevice::query()->where('device_id', $deviceId)->firstOrFail();
    }

    private function service(): LibreSolarBmsService {
        return $this->app->make(LibreSolarBmsService::class);
    }

    public function testEverySummaryIsKeptInHistory(): void {
        $service = $this->service();
        $service->recordSummary($this->summary('2026-10-15T10:00:00Z'));
        $service->recordSummary($this->summary('2026-10-15T10:15:00Z', ['soc_pct' => 71.0]));

        $this->assertSame(2, LibreSolarBmsSummary::query()->count());
        $this->assertSame('71.0', (string) $this->current('bms-1')->soc_pct);
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
        $this->assertSame('71.0', (string) $this->current('bms-1')->soc_pct);
    }

    public function testNullFieldsAndReasonsAreStored(): void {
        $this->service()->recordSummary(
            $this->summary('2026-10-15T10:00:00Z', [
                'soc_pct' => null, 'health' => 'amber',
                'health_reasons' => ['Low charge'], 'active_faults' => ['CELL_UNDERVOLTAGE'],
            ])
        );

        $row = LibreSolarBmsSummary::query()->firstOrFail();
        $this->assertNull($row->soc_pct);
        $this->assertSame(['Low charge'], $row->health_reasons);
        $this->assertSame(['CELL_UNDERVOLTAGE'], $row->active_faults);
    }
}
