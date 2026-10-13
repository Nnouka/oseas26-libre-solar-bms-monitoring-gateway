<?php

namespace App\Plugins\LibreSolarBms\Models;

use App\Models\Base\BaseModel;
use Illuminate\Database\Eloquent\Relations\BelongsTo;
use Illuminate\Support\Carbon;

/**
 * @property int         $id
 * @property int         $libre_solar_bms_device_id
 * @property string      $event_key
 * @property Carbon      $occurred_at
 * @property string      $type
 * @property string|null $fault
 * @property string      $severity
 * @property string      $message
 * @property string|null $action
 */
class LibreSolarBmsEvent extends BaseModel {
    protected $table = 'libre_solar_bms_events';

    protected $casts = [
        'occurred_at' => 'datetime',
    ];

    /**
     * @return BelongsTo<LibreSolarBmsDevice, $this>
     */
    public function device(): BelongsTo {
        return $this->belongsTo(LibreSolarBmsDevice::class, 'libre_solar_bms_device_id');
    }
}
