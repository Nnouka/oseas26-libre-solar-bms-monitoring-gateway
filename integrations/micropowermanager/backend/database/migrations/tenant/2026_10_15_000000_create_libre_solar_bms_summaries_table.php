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
