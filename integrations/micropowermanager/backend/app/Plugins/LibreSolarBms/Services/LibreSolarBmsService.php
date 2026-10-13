<?php

namespace App\Plugins\LibreSolarBms\Services;

use App\Plugins\LibreSolarBms\Models\LibreSolarBmsDevice;
use App\Plugins\LibreSolarBms\Models\LibreSolarBmsEvent;
use Illuminate\Database\Eloquent\Collection;
use Illuminate\Support\Carbon;

class LibreSolarBmsService {
    public function __construct(
        private LibreSolarBmsDevice $device,
        private LibreSolarBmsEvent $event,
    ) {}

    /**
     * @param array<string, mixed> $summary
     */
    public function recordSummary(array $summary): LibreSolarBmsDevice {
        $battery = $this->findOrRegister($summary['device_id']);
        $reportedAt = Carbon::parse($summary['ts']);

        // The ingestor replays queued data after an outage, so an older summary can arrive late.
        if ($battery->summary_at !== null && $reportedAt->lessThan($battery->summary_at)) {
            return $battery;
        }

        $battery->fill([
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
            'summary_at' => $reportedAt,
            'last_seen_at' => $reportedAt,
        ])->save();

        return $battery;
    }

    /**
     * Idempotent: the same event delivered twice is stored once.
     *
     * @param list<array<string, mixed>> $events
     *
     * @return array{accepted: int, duplicates: int}
     */
    public function recordEvents(array $events): array {
        $accepted = 0;
        $duplicates = 0;

        foreach ($events as $incoming) {
            $battery = $this->findOrRegister($incoming['device_id']);
            $fault = $incoming['fault'] ?? null;
            $stored = $this->event->newQuery()->firstOrCreate(
                ['event_key' => sha1(implode('|', [$incoming['device_id'], $incoming['ts'], $incoming['type'], $fault]))],
                [
                    'libre_solar_bms_device_id' => $battery->id,
                    'occurred_at' => Carbon::parse($incoming['ts']),
                    'type' => $incoming['type'],
                    'fault' => $fault,
                    'severity' => $incoming['severity'],
                    'message' => $incoming['message'],
                    'action' => $incoming['action'] ?? null,
                ],
            );
            $stored->wasRecentlyCreated ? $accepted++ : $duplicates++;
        }

        return ['accepted' => $accepted, 'duplicates' => $duplicates];
    }

    /**
     * @return Collection<int, LibreSolarBmsDevice>
     */
    public function listBatteries(): Collection {
        return $this->device->newQuery()->orderBy('name')->get();
    }

    public function getBattery(int $batteryId): LibreSolarBmsDevice {
        return $this->device->newQuery()->findOrFail($batteryId);
    }

    /**
     * @return Collection<int, LibreSolarBmsEvent>
     */
    public function latestEvents(int $batteryId, int $limit): Collection {
        return $this->event->newQuery()
            ->where('libre_solar_bms_device_id', $batteryId)
            ->orderByDesc('occurred_at')
            ->orderByDesc('id')
            ->limit($limit)
            ->get();
    }

    private function findOrRegister(string $deviceId): LibreSolarBmsDevice {
        return $this->device->newQuery()->firstOrCreate(['device_id' => $deviceId], ['name' => $deviceId]);
    }
}
