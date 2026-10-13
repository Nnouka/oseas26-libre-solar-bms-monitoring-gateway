<?php

namespace App\Plugins\LibreSolarBms\Models;

use App\Models\Base\BaseModel;
use Illuminate\Database\Eloquent\Relations\HasMany;
use Illuminate\Support\Carbon;

/**
 * Latest known state of one battery (a Libre Solar BMS). Only the most recent summary is kept:
 * MPM is not a time-series store, the history lives in Home Assistant / Prospect.
 *
 * @property      int                                       $id
 * @property      string                                    $device_id
 * @property      string                                    $name
 * @property      string|null                               $state
 * @property      string|null                               $soc_pct
 * @property      string|null                               $soh_pct
 * @property      string|null                               $cycles
 * @property      string|null                               $pack_voltage_v
 * @property      string|null                               $imbalance_v
 * @property      string|null                               $max_temp_c
 * @property      string|null                               $health
 * @property      int|null                                  $health_score
 * @property      list<string>|null                         $health_reasons
 * @property      list<string>|null                         $active_faults
 * @property      Carbon|null                               $summary_at
 * @property      Carbon|null                               $last_seen_at
 * @property-read \Illuminate\Database\Eloquent\Collection<int, LibreSolarBmsEvent> $events
 */
class LibreSolarBmsDevice extends BaseModel {
    protected $table = 'libre_solar_bms_devices';

    protected $casts = [
        'health_reasons' => 'array',
        'active_faults' => 'array',
        'summary_at' => 'datetime',
        'last_seen_at' => 'datetime',
    ];

    /**
     * @return HasMany<LibreSolarBmsEvent, $this>
     */
    public function events(): HasMany {
        return $this->hasMany(LibreSolarBmsEvent::class, 'libre_solar_bms_device_id');
    }
}
