CREATE TABLE vm.guest_initializations (
 tenant_id uuid NOT NULL, vm_id uuid NOT NULL,
 credential_ciphertext bytea NOT NULL, credential_fingerprint bytea NOT NULL,
 PRIMARY KEY(tenant_id,vm_id),
 FOREIGN KEY(tenant_id,vm_id) REFERENCES vm.virtual_machines(tenant_id,id)
);
