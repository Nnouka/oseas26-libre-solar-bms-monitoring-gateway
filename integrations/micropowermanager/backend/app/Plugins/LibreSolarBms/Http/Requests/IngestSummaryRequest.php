<?php

namespace App\Plugins\LibreSolarBms\Http\Requests;

use Illuminate\Foundation\Http\FormRequest;

class IngestSummaryRequest extends FormRequest {
    /**
     * @return array<string, mixed>
     */
    public function rules(): array {
        return [
            'device_id' => ['required', 'string', 'max:100'],
            'ts' => ['required', 'date'],
            'state' => ['nullable', 'string', 'max:20'],
            'soc_pct' => ['nullable', 'numeric', 'between:0,100'],
            'soh_pct' => ['nullable', 'numeric', 'between:0,100'],
            'cycles' => ['nullable', 'numeric', 'min:0'],
            'pack_voltage_v' => ['nullable', 'numeric'],
            'imbalance_v' => ['nullable', 'numeric', 'min:0'],
            'max_temp_c' => ['nullable', 'numeric'],
            'health' => ['required', 'in:green,amber,red'],
            'health_score' => ['nullable', 'integer', 'between:0,100'],
            'health_reasons' => ['nullable', 'array'],
            'health_reasons.*' => ['string'],
            'active_faults' => ['nullable', 'array'],
            'active_faults.*' => ['string'],
        ];
    }
}
