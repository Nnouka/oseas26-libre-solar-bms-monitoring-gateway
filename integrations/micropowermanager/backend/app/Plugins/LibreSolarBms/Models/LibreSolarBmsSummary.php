<?php

namespace App\Plugins\LibreSolarBms\Models;

use App\Models\Base\BaseModel;
use Illuminate\Database\Eloquent\Relations\BelongsTo;
use Illuminate\Support\Carbon;

/**
 * One health summary as reported by the gateway. Every summary is kept so operators can review history.
 *
 * @property int               $id
 * @property int               $libre_solar_bms_device_id
 * @property string            $summary_key
 * @property Carbon            $reported_at
 * @property string|null       $state
 * @property string|null       $soc_pct
 * @property string|null       $soh_pct
 * @property string|null       $cycles
 * @property string|null       $pack_voltage_v
 * @property string|null       $imbalance_v
 * @property string|null       $max_temp_c
 * @property string            $health
 * @property int|null          $health_score
 * @property list<string>|null $health_reasons
 * @property list<string>|null $active_faults
 */
class LibreSolarBmsSummary extends BaseModel {
    protected $table = 'libre_solar_bms_summaries';

    protected $casts = [
        'reported_at' => 'datetime',
        'health_reasons' => 'array',
        'active_faults' => 'array',
    ];

    /**
     * @return BelongsTo<LibreSolarBmsDevice, $this>
     */
    public function device(): BelongsTo {
        return $this->belongsTo(LibreSolarBmsDevice::class, 'libre_solar_bms_device_id');
    }
}
