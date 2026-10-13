<?php

namespace App\Plugins\LibreSolarBms\Http\Requests;

use Illuminate\Foundation\Http\FormRequest;

class IngestEventsRequest extends FormRequest {
    /**
     * @return array<string, mixed>
     */
    public function rules(): array {
        return [
            'events' => ['required', 'array', 'min:1', 'max:200'],
            'events.*.device_id' => ['required', 'string', 'max:100'],
            'events.*.ts' => ['required', 'date'],
            'events.*.type' => ['required', 'in:fault_raised,fault_cleared,device_online,device_offline'],
            'events.*.fault' => ['nullable', 'string', 'max:50'],
            'events.*.severity' => ['required', 'in:critical,warning,info'],
            'events.*.message' => ['required', 'string', 'max:255'],
            'events.*.action' => ['nullable', 'string'],
        ];
    }
}
