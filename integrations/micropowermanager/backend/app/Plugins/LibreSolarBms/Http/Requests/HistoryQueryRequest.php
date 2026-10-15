<?php

namespace App\Plugins\LibreSolarBms\Http\Requests;

use Illuminate\Foundation\Http\FormRequest;

class HistoryQueryRequest extends FormRequest {
    /**
     * @return array<string, mixed>
     */
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
}
