import { describe, it, expect, vi } from 'vitest'
import { render, screen, fireEvent } from '@testing-library/react'
import { Field } from './Field'
import { TextInput, TextArea, Select, Checkbox, SearchInput } from './inputs'
import { NumberInput, toNumeric } from './NumberInput'

// These assert the RESULT in the rendered DOM (what a screen reader and a keyboard user get),
// not that the components were called.

describe('Field wiring', () => {
  it('associates the label with the input rendered inside it', () => {
    render(
      <Field label="Име">
        <TextInput value="" onChange={() => {}} />
      </Field>,
    )
    // getByLabelText only finds it if htmlFor/id actually match.
    expect(screen.getByLabelText('Име')).toBeTruthy()
  })

  it('marks the input invalid and points at the error text when error is set', () => {
    render(
      <Field label="Цена" error="твърде малко">
        <TextInput value="" onChange={() => {}} />
      </Field>,
    )
    const input = screen.getByLabelText('Цена')
    expect(input.getAttribute('aria-invalid')).toBe('true')
    const descId = input.getAttribute('aria-describedby')
    expect(descId).toBeTruthy()
    expect(document.getElementById(descId as string)?.textContent).toBe('твърде малко')
    expect(screen.getByRole('alert').textContent).toBe('твърде малко')
  })

  it('describes the input with the hint when there is no error, and stays valid', () => {
    render(
      <Field label="Тегло" hint="в килограми">
        <NumberInput value="" onChange={() => {}} />
      </Field>,
    )
    const input = screen.getByLabelText('Тегло')
    expect(input.getAttribute('aria-invalid')).toBe(null)
    const descId = input.getAttribute('aria-describedby')
    expect(document.getElementById(descId as string)?.textContent).toBe('в килограми')
  })

  it('sets aria-required when the field is required', () => {
    render(
      <Field label="Баркод" required>
        <TextInput value="" onChange={() => {}} />
      </Field>,
    )
    expect(screen.getByLabelText(/Баркод/).getAttribute('aria-required')).toBe('true')
  })

  it('wires a textarea, a select and a search input the same way', () => {
    render(
      <>
        <Field label="Описание">
          <TextArea value="" onChange={() => {}} />
        </Field>
        <Field label="Мярка">
          <Select value="бр" options={['бр', 'кг']} onChange={() => {}} />
        </Field>
        <Field label="Търсене">
          <SearchInput value="" onChange={() => {}} />
        </Field>
      </>,
    )
    expect(screen.getByLabelText('Описание').tagName).toBe('TEXTAREA')
    expect(screen.getByLabelText('Мярка').tagName).toBe('SELECT')
    expect(screen.getByLabelText('Търсене').getAttribute('type')).toBe('search')
  })

  it('gives each Field its own id so two fields on one page do not collide', () => {
    render(
      <>
        <Field label="А">
          <TextInput value="" onChange={() => {}} />
        </Field>
        <Field label="Б">
          <TextInput value="" onChange={() => {}} />
        </Field>
      </>,
    )
    const a = screen.getByLabelText('А').getAttribute('id')
    const b = screen.getByLabelText('Б').getAttribute('id')
    expect(a).toBeTruthy()
    expect(a).not.toBe(b)
  })
})

describe('toNumeric', () => {
  it('turns a dot decimal into the BG comma form', () => {
    expect(toNumeric('1.5')).toBe('1,5')
  })
  it('keeps only the first comma', () => {
    expect(toNumeric('1,5,7')).toBe('1,57')
    expect(toNumeric('1.5.7')).toBe('1,57')
  })
  it('strips letters, spaces and units', () => {
    expect(toNumeric('12 кг')).toBe('12')
    expect(toNumeric('abc')).toBe('')
  })
  it('leaves a plain integer alone and keeps a trailing comma while typing', () => {
    expect(toNumeric('12')).toBe('12')
    expect(toNumeric('12,')).toBe('12,')
  })
})

describe('NumberInput', () => {
  it('emits the normalised value, not the raw keystrokes', () => {
    const onChange = vi.fn()
    render(
      <Field label="Тегло">
        <NumberInput value="" onChange={onChange} unit="кг" />
      </Field>,
    )
    fireEvent.change(screen.getByLabelText('Тегло'), { target: { value: '2.75kg' } })
    expect(onChange).toHaveBeenCalledWith('2,75')
  })
})

describe('Select', () => {
  it('renders a leading empty option only when a placeholder is given', () => {
    const { rerender } = render(<Select value="" options={['a', 'b']} onChange={() => {}} />)
    expect(screen.getAllByRole('option')).toHaveLength(2)
    rerender(<Select value="" options={['a', 'b']} placeholder="— избери —" onChange={() => {}} />)
    const opts = screen.getAllByRole('option')
    expect(opts).toHaveLength(3)
    expect((opts[0] as HTMLOptionElement).value).toBe('')
  })

  it('supports value/label pairs and reports the value', () => {
    const onChange = vi.fn()
    render(<Select value="1" options={[{ value: '1', label: 'едно' }, { value: '2', label: 'две' }]} onChange={onChange} />)
    fireEvent.change(screen.getByRole('combobox'), { target: { value: '2' } })
    expect(onChange).toHaveBeenCalledWith('2')
  })
})

describe('Checkbox', () => {
  it('toggles through its own label text', () => {
    const onChange = vi.fn()
    render(<Checkbox checked={false} onChange={onChange} label="Продавай под нула" />)
    fireEvent.click(screen.getByLabelText('Продавай под нула'))
    expect(onChange).toHaveBeenCalledWith(true)
  })
})
