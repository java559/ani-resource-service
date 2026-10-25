package vm

import (
	"context"
	"crypto/aes"
	"crypto/cipher"
	"crypto/hmac"
	"crypto/rand"
	"crypto/sha256"
	"crypto/subtle"
	"encoding/hex"
	"encoding/json"
	"fmt"

	"github.com/GehirnInc/crypt/sha512_crypt"
	"github.com/jackc/pgx/v5"
	biz "github.com/zhangzhe-ctrl/ani-resource-service/internal/biz/vm"
)

type guestCredentialCipher struct {
	aead           cipher.AEAD
	fingerprintKey []byte
}

// ConfigureGuestCredentialKey is called once at composition, from a private
// 32-byte file. No credential is stored in ordinary VM/spec/operation records.
func (p *Postgres) ConfigureGuestCredentialKey(key []byte) error {
	if len(key) != 32 {
		return biz.ErrInvalid
	}
	derive := func(label string) []byte { m := hmac.New(sha256.New, key); m.Write([]byte(label)); return m.Sum(nil) }
	block, e := aes.NewCipher(derive("ani/vm/guest-credentials/aead/v1"))
	if e != nil {
		return e
	}
	aead, e := cipher.NewGCM(block)
	if e != nil {
		return e
	}
	p.guestCipher = &guestCredentialCipher{aead: aead, fingerprintKey: derive("ani/vm/guest-credentials/fingerprint/v1")}
	return nil
}
func credentialAAD(a biz.Acceptance) []byte {
	b, _ := json.Marshal([]string{"ani/vm/guest-credentials/v1", a.TenantID, a.VMID, a.OperationID, a.Spec.Digest})
	return b
}
func (c *guestCredentialCipher) fingerprint(a biz.Acceptance) []byte {
	m := hmac.New(sha256.New, c.fingerprintKey)
	m.Write(credentialAAD(a))
	m.Write([]byte{0})
	m.Write([]byte(a.Password))
	return m.Sum(nil)
}
func (p *Postgres) checkGuestCredentialReplay(ctx context.Context, tx pgx.Tx, a biz.Acceptance) error {
	if a.Spec.GuestInitialization == nil {
		if a.Password != "" {
			return biz.ErrInvalid
		}
		return nil
	}
	if p.guestCipher == nil {
		return biz.ErrDependency
	}
	var got []byte
	if e := tx.QueryRow(ctx, "SELECT credential_fingerprint FROM vm.guest_initializations WHERE tenant_id=$1 AND vm_id=$2", a.TenantID, a.VMID).Scan(&got); e != nil {
		return biz.ErrIdempotency
	}
	if subtle.ConstantTimeCompare(got, p.guestCipher.fingerprint(a)) != 1 {
		return biz.ErrIdempotency
	}
	return nil
}
func (p *Postgres) persistGuestCredentials(ctx context.Context, tx pgx.Tx, a biz.Acceptance) error {
	if a.Spec.GuestInitialization == nil {
		if a.Password != "" {
			return biz.ErrInvalid
		}
		return nil
	}
	if p.guestCipher == nil {
		return biz.ErrDependency
	}
	if a.Spec.GuestInitialization.PasswordSet != (a.Password != "") {
		return biz.ErrInvalid
	}
	hashed := ""
	if a.Password != "" {
		salt := make([]byte, 8)
		if _, e := rand.Read(salt); e != nil {
			return e
		}
		var e error
		hashed, e = sha512_crypt.New().Generate([]byte(a.Password), []byte("$6$rounds=100000$"+hex.EncodeToString(salt)))
		if e != nil {
			return fmt.Errorf("guest credential hashing failed")
		}
	}
	nonce := make([]byte, p.guestCipher.aead.NonceSize())
	if _, e := rand.Read(nonce); e != nil {
		return e
	}
	protected := p.guestCipher.aead.Seal(nonce, nonce, []byte(hashed), credentialAAD(a))
	_, e := tx.Exec(ctx, "INSERT INTO vm.guest_initializations(tenant_id,vm_id,credential_ciphertext,credential_fingerprint) VALUES($1,$2,$3,$4)", a.TenantID, a.VMID, protected, p.guestCipher.fingerprint(a))
	return e
}

// GuestPasswordHash is an outbound-only credential port, never a business RPC.
func (p *Postgres) GuestPasswordHash(ctx context.Context, v biz.VM) (string, error) {
	if p.guestCipher == nil {
		return "", biz.ErrDependency
	}
	var encrypted []byte
	if e := p.pool.QueryRow(ctx, "SELECT credential_ciphertext FROM vm.guest_initializations WHERE tenant_id=$1 AND vm_id=$2", v.TenantID, v.ID).Scan(&encrypted); e != nil {
		return "", notFound(e)
	}
	size := p.guestCipher.aead.NonceSize()
	if len(encrypted) < size {
		return "", biz.ErrChanged
	}
	plain, e := p.guestCipher.aead.Open(nil, encrypted[:size], encrypted[size:], credentialAAD(biz.Acceptance{TenantID: v.TenantID, VMID: v.ID, OperationID: v.CreateOperationID, Spec: v.Spec}))
	if e != nil {
		return "", biz.ErrChanged
	}
	defer clear(plain)
	return string(plain), nil
}
